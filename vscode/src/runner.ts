// Runs autodoc_json.py for one source text and returns the parsed document, or a
// classified failure the panel can explain.  No `vscode` import: unit-testable on its own.
import { spawn } from 'node:child_process';
import { homedir } from 'node:os';
import type { Document, Language } from './types.ts';

export interface RunOptions {
  /** The `hdlAutodoc.pythonPath` setting; empty means find a suitable Python. */
  python: string;
  /** Replaces `${workspaceFolder}` in the setting. */
  workspaceFolder?: string;
  /** Interpreters to try when none is named, each as a command and its leading arguments. */
  candidates?: string[][];
  /** How long a version probe may take (default 5000 ms). */
  probeTimeoutMs?: number;
  script: string;
  language: Language;
  text: string;
  support: string[];
  busGroups?: string;
  timeoutMs: number;
  signal?: AbortSignal;
}

export type FailureKind = 'no-python' | 'old-python' | 'timeout' | 'aborted' | 'failed' | 'bad-json';

export type RunResult =
  | { ok: true; doc: Document }
  | { ok: false; kind: FailureKind; message: string };

const MIN_MAJOR = 3;
const MIN_MINOR = 11;
const PROBE_CODE = 'import sys; print("%d.%d" % sys.version_info[:2])';

const versions = new Map<string, Promise<[number, number] | null>>();

/** Forget the probed interpreter versions (tests, and after the setting changes). */
export function resetVersionCache(): void {
  versions.clear();
}

const PROBE_TIMEOUT_MS = 5000;

function probe(command: string[], timeoutMs: number): Promise<[number, number] | null> {
  return new Promise((resolve) => {
    const [program, ...leading] = command;
    const child = spawn(program, [...leading, '-c', PROBE_CODE], { stdio: ['ignore', 'pipe', 'ignore'] });
    let out = '';
    // A wrapper that never answers must not leave every refresh waiting on it.
    const timer = setTimeout(() => { child.kill('SIGKILL'); resolve(null); }, timeoutMs);
    child.stdout.on('data', (chunk: Buffer) => { out += chunk.toString('utf8'); });
    child.on('error', () => { clearTimeout(timer); resolve(null); });
    child.on('close', () => {
      clearTimeout(timer);
      const m = /^(\d+)\.(\d+)/.exec(out.trim());
      resolve(m ? [Number(m[1]), Number(m[2])] : null);
    });
  });
}

async function interpreterVersion(command: string[], timeoutMs: number): Promise<[number, number] | null> {
  const key = command.join('\0');
  let pending = versions.get(key);
  if (!pending) {
    pending = probe(command, timeoutMs);
    versions.set(key, pending);
  }
  const version = await pending;
  if (version === null) versions.delete(key);          // not found or unusable: look again next time
  return version;
}

const isRecent = (v: [number, number]): boolean => v[0] > MIN_MAJOR || (v[0] === MIN_MAJOR && v[1] >= MIN_MINOR);

/** Where Python usually is, in order.  The absolute paths cover a VS Code started from the Dock,
 *  whose PATH lacks Homebrew's and pyenv's folders. */
function defaultCandidates(): string[][] {
  return [
    ['python3'], ['python'],
    ...(process.platform === 'win32' ? [['py', '-3']] : []),
    ['python3.13'], ['python3.12'], ['python3.11'],
    ...(process.platform === 'win32' ? [] : [['/opt/homebrew/bin/python3'], ['/usr/local/bin/python3']]),
  ];
}

/** `~` and `${workspaceFolder}` in the setting. */
function expandSetting(setting: string, workspaceFolder?: string): string {
  let value = setting.trim();
  if (workspaceFolder) value = value.replace(/\$\{workspaceFolder\}/g, workspaceFolder);
  if (value === '~' || value.startsWith('~/') || value.startsWith('~\\')) value = homedir() + value.slice(1);
  return value;
}

type Resolved = { ok: true; command: string[] } | { ok: false; kind: FailureKind; message: string };

async function resolveInterpreter(o: RunOptions): Promise<Resolved> {
  const timeout = o.probeTimeoutMs ?? PROBE_TIMEOUT_MS;
  const named = expandSetting(o.python, o.workspaceFolder);
  const tried = named ? [[named]] : (o.candidates ?? defaultCandidates());
  const old: string[] = [];

  for (const command of tried) {
    const version = await interpreterVersion(command, timeout);
    if (version === null) continue;
    if (isRecent(version)) return { ok: true, command };
    old.push(`${command.join(' ')} is Python ${version[0]}.${version[1]}`);
  }

  const need = `Set hdlAutodoc.pythonPath to a Python ${MIN_MAJOR}.${MIN_MINOR} or newer.`;
  if (old.length > 0) {
    return { ok: false, kind: 'old-python',
      message: `HDL AutoDoc needs Python ${MIN_MAJOR}.${MIN_MINOR} or newer, but only older ones were found (${old.join('; ')}). ${need}` };
  }
  return { ok: false, kind: 'no-python',
    message: named ? `Could not run "${named}". ${need}`
      : `No Python was found (tried ${tried.map((c) => c.join(' ')).join(', ')}). ${need}` };
}

const fail = (kind: FailureKind, message: string): RunResult => ({ ok: false, kind, message });

export async function runAutodocJson(opts: RunOptions): Promise<RunResult> {
  if (opts.signal?.aborted) return fail('aborted', 'The refresh was superseded.');

  const python = await resolveInterpreter(opts);
  if (!python.ok) return fail(python.kind, python.message);
  if (opts.signal?.aborted) return fail('aborted', 'The refresh was superseded.');

  const args = [opts.script, '--language', opts.language];
  if (opts.support.length > 0) args.push('--support', ...opts.support);
  if (opts.busGroups) args.push('--bus-groups', opts.busGroups);

  return new Promise<RunResult>((resolve) => {
    const [program, ...leading] = python.command;
    const child = spawn(program, [...leading, ...args], { stdio: ['pipe', 'pipe', 'pipe'] });
    const out: Buffer[] = [];
    const err: Buffer[] = [];
    let settled = false;

    const finish = (result: RunResult): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      opts.signal?.removeEventListener('abort', onAbort);
      resolve(result);
    };
    const kill = (): void => { child.kill('SIGKILL'); };
    const onAbort = (): void => { kill(); finish(fail('aborted', 'The refresh was superseded.')); };
    const timer = setTimeout(() => {
      kill();
      finish(fail('timeout', `Reading the file took longer than ${opts.timeoutMs} ms.`));
    }, opts.timeoutMs);
    opts.signal?.addEventListener('abort', onAbort, { once: true });

    child.stdout.on('data', (c: Buffer) => out.push(c));
    child.stderr.on('data', (c: Buffer) => err.push(c));
    child.stdin.on('error', () => { /* the child exited before reading everything */ });
    child.on('error', (e) => finish(fail('no-python', `Could not run "${python.command.join(' ')}": ${e.message}`)));
    child.on('close', (code) => {
      if (code !== 0) {
        const detail = Buffer.concat(err).toString('utf8').trim().slice(0, 2000);
        finish(fail('failed', `autodoc_json.py exited with status ${code}${detail ? `: ${detail}` : ''}`));
        return;
      }
      try {
        finish({ ok: true, doc: JSON.parse(Buffer.concat(out).toString('utf8')) as Document });
      } catch (e) {
        finish(fail('bad-json', `autodoc_json.py wrote output that is not JSON (${(e as Error).message}).`));
      }
    });

    child.stdin.end(Buffer.from(opts.text, 'utf8'));
  });
}
