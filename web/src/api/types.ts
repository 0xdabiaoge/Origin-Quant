/** Typed-ish shapes for the engine's JSON. Kept loose on purpose: the API is
 *  the source of truth and a stale frontend type should never block a deploy. */

export type Money = string;

export interface Brand {
  name: string;
  tagline: string;
  mode: string;
  version: string;
  live_trading: boolean;
}

export interface TradeStats {
  trades: number;
  net_pnl: Money;
  fees: Money;
  win_rate: number;
  profit_factor: Money | null;
}

export interface EquityPoint {
  ts_ms: number;
  equity: Money;
  cash?: Money;
  gross_notional?: Money;
  net_notional?: Money;
  open_risk?: Money;
  heat?: Money;
  drawdown: Money;
  day_pnl?: Money;
  week_pnl?: Money;
}

export interface Position {
  symbol: string;
  book: string;
  qty: Money;
  avg_px: Money;
  mark_px?: Money | null;
  stop_px?: Money | null;
  initial_stop_px?: Money | null;
  strategy_id: string;
  realized_pnl: Money;
  funding_net: Money;
  fees_paid?: Money;
  opened_ms: number;
  tags?: Record<string, string>;
}

export interface Decision {
  id: string;
  ts_ms: number;
  cycle_id?: string;
  strategy_id: string;
  book: string;
  action: string;
  symbols: string;
  approved: number;
  gates: string;
}

export interface Order {
  client_id: string;
  symbol: string;
  side: string;
  order_type: string;
  qty: Money;
  filled_qty: Money;
  avg_px: Money | null;
  status: string;
  fee: Money;
  reduce_only: number;
  created_ms: number;
}

export interface Fill {
  ts_ms: number;
  symbol: string;
  side: string;
  qty: Money;
  px: Money;
  fee: Money;
  liquidity: string;
  order_client_id: string;
}

export interface EventRow {
  id: number;
  ts_ms: number;
  type: string;
  symbol: string | null;
  payload: string;
}

export interface Overview {
  equity: Money | null;
  cash: Money | null;
  positions: Position[];
  stats: TradeStats;
  equity_series: EquityPoint[];
  recent_decisions: Decision[];
  counts: Record<string, number>;
  last_cycle_ms: number;
  mode: string;
  version: string;
}

export interface AdminOverview {
  version: string;
  uptime_s: number;
  counts: Record<string, number>;
  first_event_ms: number | null;
  last_event_ms: number | null;
  events_by_type: { type: string; n: number }[];
  stats: TradeStats;
  mode: string;
  scheduler: boolean;
  config_hash: string;
}

export interface UserRow {
  id: number;
  username: string;
  role: string;
  created_ms: number;
  last_login_ms: number | null;
}

export interface AuditRow {
  id: number;
  ts_ms: number;
  actor: string;
  action: string;
  detail: unknown;
}

export interface InstrumentFreshness {
  symbol: string;
  last_bar_ms: number | null;
  age_min: number | null;
  stale: boolean;
}

export interface Me {
  username: string;
  role: string;
  via: string;
}
export interface PublicMetrics {
  mode: string;
  version: string;
  uptime_s: number;
  last_cycle_ms: number;
  stats: TradeStats;
  equity: { ts_ms: number; equity: Money; drawdown: Money }[];
}

export interface PositionsPayload {
  positions: Position[];
  equity: Money | null;
  cash: Money | null;
  heat?: Money;
  open_risk?: Money;
}

export interface OrdersPayload {
  open: Order[];
  fills: Fill[];
}

export interface DecisionsPayload {
  items: Decision[];
  rejections: { gate: string; n: number }[];
}

export interface DataPayload {
  timeframe: string;
  now_ms: number;
  instruments: InstrumentFreshness[];
}

export interface BacktestResult {
  report?: string;
  metrics?: Record<string, unknown>;
  trades?: unknown[];
  equity_curve?: unknown[];
  error?: string;
}
