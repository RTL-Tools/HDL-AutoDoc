import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runAutodocJson, resetVersionCache } from '../src/runner.ts';

const fake = fileURLToPath(new URL('./fixtures/fake-python', import.meta.url));
const base = { python: fake, script: '/x/autodoc_json.py', language: 'vhdl', text: 'entity e is end;',
               support: [], timeoutMs: 5000 };

let scratch;
beforeEach(() => {
  scratch = mkdtempSync(join(tmpdir(), 'runner-'));
  resetVersionCache();
  for (const k of ['FAKE_VERSION', 'FAKE_MODE', 'FAKE_PIDFILE', 'FAKE_PROBEFILE']) delete process.env[k];
});

const alive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };
const gone = async (pid) => { for (let i = 0; i < 40 && alive(pid); i++) await new Promise((r) => setTimeout(r, 50)); return !alive(pid); };

test('a successful run returns the parsed document and passes the arguments through', async () => {
  const r = await runAutodocJson({ ...base, language: 'systemverilog', support: ['/a/x.sv', '/b/y.sv'], busGroups: '/g.toml' });
  assert.equal(r.ok, true);
  assert.equal(r.doc.language, 'systemverilog');
  assert.deepEqual(JSON.parse(r.doc.warnings[0]),
    ['/x/autodoc_json.py', '--language', 'systemverilog', '--support', '/a/x.sv', '/b/y.sv', '--bus-groups', '/g.toml']);
});

test('no --support or --bus-groups flags are passed when there are none', async () => {
  const r = await runAutodocJson(base);
  assert.deepEqual(JSON.parse(r.doc.warnings[0]), ['/x/autodoc_json.py', '--language', 'vhdl']);
});

test('the source text reaches the interpreter unchanged (CRLF, non-ASCII)', async () => {
  process.env.FAKE_MODE = 'echo';
  const text = 'entity é is\r\n-- Zählt Anfragen — naïve.\r\nend;\r\n';
  const r = await runAutodocJson({ ...base, text });
  assert.equal(r.doc.module.description, text);
});

test('a missing interpreter is reported with the setting to change', async () => {
  const r = await runAutodocJson({ ...base, python: '/definitely/not/python' });
  assert.equal(r.ok, false);
  assert.equal(r.kind, 'no-python');
  assert.match(r.message, /hdlAutodoc\.pythonPath/);
  assert.match(r.message, /\/definitely\/not\/python/);
});

test('a Python older than 3.11 is reported with both versions', async () => {
  process.env.FAKE_VERSION = '3.9';
  const r = await runAutodocJson(base);
  assert.equal(r.kind, 'old-python');
  assert.match(r.message, /3\.9/);
  assert.match(r.message, /3\.11/);
});

test('3.11 and newer are accepted, including 3.100', async () => {
  for (const v of ['3.11', '3.13', '3.100']) {
    resetVersionCache();
    process.env.FAKE_VERSION = v;
    assert.equal((await runAutodocJson(base)).ok, true, v);
  }
});

test('the version of an interpreter is probed once, not on every run', async () => {
  const probes = join(mkdtempSync(join(tmpdir(), 'probe-')), 'probes');
  process.env.FAKE_PROBEFILE = probes;
  await runAutodocJson(base);
  await runAutodocJson(base);
  assert.equal(readFileSync(probes, 'utf8').trim().split('\n').length, 1);
});

test('a run that takes too long is killed and reported as a timeout', async () => {
  const pidfile = join(mkdtempSync(join(tmpdir(), 'pid-')), 'pid');
  process.env.FAKE_MODE = 'hang';
  process.env.FAKE_PIDFILE = pidfile;
  const started = Date.now();
  const r = await runAutodocJson({ ...base, timeoutMs: 300 });
  assert.equal(r.kind, 'timeout');
  assert.ok(Date.now() - started < 3000);
  assert.ok(await gone(Number(readFileSync(pidfile, 'utf8'))), 'the child is still running');
});

test('aborting a run kills the child and reports it as aborted', async () => {
  const pidfile = join(mkdtempSync(join(tmpdir(), 'pid-')), 'pid');
  process.env.FAKE_MODE = 'hang';
  process.env.FAKE_PIDFILE = pidfile;
  const controller = new AbortController();
  const pending = runAutodocJson({ ...base, signal: controller.signal });
  setTimeout(() => controller.abort(), 300);
  const r = await pending;
  assert.equal(r.kind, 'aborted');
  assert.ok(await gone(Number(readFileSync(pidfile, 'utf8'))), 'the child is still running');
});

test('an already aborted signal never starts a process', async () => {
  const pidfile = join(mkdtempSync(join(tmpdir(), 'pid-')), 'pid');
  process.env.FAKE_PIDFILE = pidfile;
  const controller = new AbortController();
  controller.abort();
  const r = await runAutodocJson({ ...base, signal: controller.signal });
  assert.equal(r.kind, 'aborted');
  assert.equal(existsSync(pidfile), false);
});

test('a non-zero exit is a failure that carries the error output', async () => {
  process.env.FAKE_MODE = 'fail';
  const r = await runAutodocJson(base);
  assert.equal(r.kind, 'failed');
  assert.match(r.message, /something broke/);
});

test('output that is not JSON is reported as such', async () => {
  process.env.FAKE_MODE = 'badjson';
  const r = await runAutodocJson(base);
  assert.equal(r.kind, 'bad-json');
});

test('the runner works with the real autodoc_json.py', async () => {
  const script = fileURLToPath(new URL('../../src/scripts/hdl_autodoc/autodoc_json.py', import.meta.url));
  const text = readFileSync(fileURLToPath(new URL('../../example_full/src/sample_ctrl.vhd', import.meta.url)), 'utf8');
  const r = await runAutodocJson({ ...base, python: 'python3', script, text });
  assert.equal(r.ok, true, r.message);
  assert.equal(r.doc.module.name, 'sample_ctrl');
  assert.equal(r.doc.fsm.states.length, 4);
  assert.ok(r.doc.processes.some((p) => p.label === 'p_next_state' && p.mermaid.length === 1));
});

// ── finding a suitable Python when none is named ─────────────────────────────

import { chmodSync } from 'node:fs';
import { homedir } from 'node:os';

/** An interpreter that reports `version`, as a small shell script that runs the fake. */
function interpreter(name, version) {
  const file = join(scratch, name);
  writeFileSync(file, `#!/bin/sh\nFAKE_VERSION=${version} exec "${fake}" "$@"\n`);
  chmodSync(file, 0o755);
  return file;
}
const auto = (candidates, over = {}) =>
  runAutodocJson({ ...base, python: '', candidates, ...over });

test('with no interpreter named, the first candidate that is 3.11 or newer is used', async () => {
  const old = interpreter('py39', '3.9');
  const good = interpreter('py312', '3.12');
  const later = interpreter('py313', '3.13');
  const r = await auto([['/no/such/python'], [old], [good], [later]]);
  assert.equal(r.ok, true, r.message);
});

test('candidates are tried in order and the one that ran is the first suitable one', async () => {
  process.env.FAKE_PROBEFILE = join(scratch, 'probes');
  const good = interpreter('py312', '3.12');
  const later = interpreter('py313', '3.13');
  await auto([[good], [later]]);
  const probed = readFileSync(join(scratch, 'probes'), 'utf8').trim().split('\n').length;
  assert.equal(probed, 1, 'the search stops at the first suitable interpreter');
});

test('a candidate with arguments (py -3) gets them before the script', async () => {
  const launcher = interpreter('py', '3.12');
  const r = await auto([[launcher, '-3']]);
  assert.equal(r.ok, true, r.message);
  assert.deepEqual(JSON.parse(r.doc.warnings[0]).slice(0, 2), ['-3', '/x/autodoc_json.py']);
});

test('only old interpreters: the message names each one found and the setting to change', async () => {
  const a = interpreter('py39', '3.9');
  const b = interpreter('py310', '3.10');
  const r = await auto([[a], [b]]);
  assert.equal(r.kind, 'old-python');
  assert.match(r.message, /3\.9/);
  assert.match(r.message, /3\.10/);
  assert.match(r.message, /3\.11/);
  assert.match(r.message, /hdlAutodoc\.pythonPath/);
});

test('no interpreter at all: the message says what was tried and names the setting', async () => {
  const r = await auto([['/no/python-a'], ['/no/python-b']]);
  assert.equal(r.kind, 'no-python');
  assert.match(r.message, /python-a/);
  assert.match(r.message, /hdlAutodoc\.pythonPath/);
});

test('an empty setting means "find one", not an error', async () => {
  const good = interpreter('py312', '3.12');
  assert.equal((await auto([[good]], { python: '   ' })).ok, true);
});

test('a named interpreter is used as it is and never replaced by a candidate', async () => {
  const good = interpreter('py312', '3.12');
  const r = await auto([[good]], { python: '/definitely/not/python' });
  assert.equal(r.kind, 'no-python');
  assert.match(r.message, /definitely\/not\/python/);
});

test('~ and ${workspaceFolder} in the setting are expanded', async () => {
  const folder = mkdtempSync(join(tmpdir(), 'ws-'));
  const venv = join(folder, '.venv');
  mkdirSync(venv);
  writeFileSync(join(venv, 'python'), `#!/bin/sh\nFAKE_VERSION=3.12 exec "${fake}" "$@"\n`);
  chmodSync(join(venv, 'python'), 0o755);
  const r = await auto([], { python: '${workspaceFolder}/.venv/python', workspaceFolder: folder });
  assert.equal(r.ok, true, r.message);
  const home = mkdtempSync(join(homedir(), '.hdl-autodoc-test-'));
  try {
    writeFileSync(join(home, 'python'), `#!/bin/sh\nFAKE_VERSION=3.12 exec "${fake}" "$@"\n`);
    chmodSync(join(home, 'python'), 0o755);
    const tilde = `~/${home.slice(homedir().length + 1)}/python`;
    assert.equal((await auto([], { python: tilde })).ok, true);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test('a probe that never answers is given up on, and the search goes on', async () => {
  const hanging = join(scratch, 'hang');
  writeFileSync(hanging, `#!/bin/sh\nFAKE_PROBE_HANG=1 exec "${fake}" "$@"\n`);
  chmodSync(hanging, 0o755);
  const good = interpreter('py312', '3.12');
  const started = Date.now();
  const r = await auto([[hanging], [good]], { probeTimeoutMs: 1500 });   // a node start-up is ~0.3 s
  assert.equal(r.ok, true, r.message);
  assert.ok(Date.now() - started < 6000);
});

test('a hanging named interpreter is reported instead of leaving the panel blank', async () => {
  const hanging = join(scratch, 'hang');
  writeFileSync(hanging, `#!/bin/sh\nFAKE_PROBE_HANG=1 exec "${fake}" "$@"\n`);
  chmodSync(hanging, 0o755);
  const r = await auto([], { python: hanging, probeTimeoutMs: 1000 });
  assert.equal(r.kind, 'no-python');
  assert.match(r.message, /hdlAutodoc\.pythonPath/);
});
