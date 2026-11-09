// The JSON document written by src/scripts/hdl_autodoc/autodoc_json.py (see its docstring).

export type Language = 'vhdl' | 'systemverilog';

export interface MermaidDiagram {
  caption: string | null;
  source: string;
}

export interface ModuleInfo {
  name: string;
  line: number;
  description: string;
  wavedrom: string[];
  mermaid: MermaidDiagram[];
}

export interface Generic {
  name: string;
  type: string;
  default: string;
  description: string;
  line: number;
}

export interface Port {
  name: string;
  direction: string;
  type: string;
  range: string;
  description: string;
  line: number;
  kind: 'port' | 'interface';
  interface: string | null;
  modport: string | null;
  group: string | null;
}

export interface Instance {
  instance: string;
  component: string;
  kind: 'module' | 'interface';
  line: number;
}

export interface ProcessInfo {
  label: string;
  kind: string;
  line: number;
  sensitivity: string[];
  description: string;
  wavedrom: string[];
  mermaid: MermaidDiagram[];
}

export interface Fsm {
  states: string[];
  line: number;                                  // the `case`
  state_lines: Record<string, number>;           // each state's arm
  transitions: { from: string; to: string; condition: string; line: number }[];
  dot: string;
}

export interface CdcAnalysis {
  domains: Record<string, string[]>;
  crossings: { signal: string; src_clock: string; dst_clock: string; synchronized: boolean }[];
  dual_clock_instances: { instance: string; component: string; clocks: Record<string, string> }[];
  lines?: Record<string, number>;                // where each clock and crossing signal is first mentioned
}

export interface ResetAnalysis {
  processes: { label: string; clock: string; reset: string | null; style: string }[];
  domains: Record<string, string[]>;
  crossings: { signal: string; src_reset: string; dst_reset: string }[];
  lines?: Record<string, number>;
}

export interface Document {
  language: Language;
  module: ModuleInfo | null;
  generics: Generic[];
  ports: Port[];
  block: { dot: string } | null;                 // Graphviz source of the block diagram
  instances: Instance[];
  processes: ProcessInfo[];
  fsm: Fsm | null;
  cdc: CdcAnalysis | null;
  reset: ResetAnalysis | null;
  warnings: string[];
}
