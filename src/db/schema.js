export const SCHEMA_VERSION = 1;

export const schemaSql = `
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resources(
  id TEXT PRIMARY KEY,
  canonical_url TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  resource_type TEXT NOT NULL DEFAULT 'web',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  archived_at TEXT,
  deleted_at TEXT,
  organization_state TEXT NOT NULL DEFAULT 'pending',
  evidence_state TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS occurrences(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  resource_id TEXT NOT NULL REFERENCES resources(id),
  browser TEXT NOT NULL,
  profile TEXT NOT NULL DEFAULT '',
  window_key TEXT NOT NULL DEFAULT '',
  tab_key TEXT NOT NULL DEFAULT '',
  group_key TEXT NOT NULL DEFAULT '',
  group_title TEXT NOT NULL DEFAULT '',
  group_color TEXT NOT NULL DEFAULT '',
  observed_url TEXT NOT NULL,
  observed_title TEXT NOT NULL DEFAULT '',
  captured_at TEXT NOT NULL,
  capture_key TEXT NOT NULL,
  UNIQUE(capture_key, browser, profile, window_key, tab_key)
);
CREATE INDEX IF NOT EXISTS occurrences_resource_idx ON occurrences(resource_id);
CREATE TABLE IF NOT EXISTS capture_runs(
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  status TEXT NOT NULL,
  summary_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS notes(
  id TEXT PRIMARY KEY,
  resource_id TEXT NOT NULL REFERENCES resources(id),
  text TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'typed',
  confirmed INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collections(
  id TEXT PRIMARY KEY,
  parent_id TEXT REFERENCES collections(id),
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  pinned INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(parent_id, name)
);
CREATE TABLE IF NOT EXISTS memberships(
  resource_id TEXT NOT NULL REFERENCES resources(id),
  collection_id TEXT NOT NULL REFERENCES collections(id),
  origin TEXT NOT NULL DEFAULT 'user',
  pinned INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY(resource_id, collection_id)
);
CREATE TABLE IF NOT EXISTS evidence(
  id TEXT PRIMARY KEY,
  resource_id TEXT NOT NULL REFERENCES resources(id),
  adapter TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  expires_at TEXT,
  status TEXT NOT NULL,
  limitation TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL,
  UNIQUE(resource_id, adapter, kind, content_hash)
);
CREATE TABLE IF NOT EXISTS proposals(
  id TEXT PRIMARY KEY,
  resource_id TEXT NOT NULL REFERENCES resources(id),
  job_id TEXT,
  proposal_json TEXT NOT NULL,
  basis_revision INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'ready',
  created_at TEXT NOT NULL,
  decided_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  scope_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  cursor INTEGER NOT NULL DEFAULT 0,
  completed_units INTEGER NOT NULL DEFAULT 0,
  total_units INTEGER NOT NULL DEFAULT 0,
  checkpoint_json TEXT NOT NULL DEFAULT '{}',
  error TEXT NOT NULL DEFAULT '',
  cancelled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS job_items(
  job_id TEXT NOT NULL REFERENCES jobs(id),
  resource_id TEXT NOT NULL REFERENCES resources(id),
  basis_revision INTEGER NOT NULL,
  evidence_fingerprint TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  result_json TEXT NOT NULL DEFAULT '{}',
  error TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(job_id, resource_id)
);
CREATE TABLE IF NOT EXISTS history(
  id TEXT PRIMARY KEY,
  action_type TEXT NOT NULL,
  action_json TEXT NOT NULL,
  inverse_json TEXT NOT NULL DEFAULT '{}',
  actor TEXT NOT NULL,
  created_at TEXT NOT NULL,
  undone_at TEXT
);
CREATE TABLE IF NOT EXISTS browser_actions(
  id TEXT PRIMARY KEY,
  action_type TEXT NOT NULL,
  resource_id TEXT,
  requested_target_json TEXT NOT NULL,
  observed_result_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL);
`;
