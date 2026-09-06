import path from 'node:path';
import fs from 'node:fs';
import { Store } from './db/store.js';
import { BrowserBridge } from './browser/bridge.js';
import { JobRunner } from './app/jobs.js';
import { CodexAppServer } from './runtime/codex-app-server.js';
import { PublicEvidenceService } from './evidence/public-evidence.js';
import { createServer } from './http/server.js';

const host=process.env.TABATLAS_HOST||'127.0.0.1', port=Number(process.env.TABATLAS_PORT||8790);
const state=path.resolve(process.env.TABATLAS_STATE_DIR||'state-v2');fs.mkdirSync(state,{recursive:true});
const store=new Store(path.join(state,'tabatlas.db'));const agentCwd=path.join(state,'agent-context');fs.mkdirSync(agentCwd,{recursive:true});const runtime=new CodexAppServer({defaultCwd:agentCwd});const bridge=new BrowserBridge({store});const jobs=new JobRunner({store,runtime});const evidence=new PublicEvidenceService();
const server=createServer({store,bridge,jobs,runtime,evidence});
server.listen(port,host,()=>console.log(`TabAtlas: http://${host}:${port}`));
const stop=()=>server.close(async()=>{await runtime.disconnect().catch(()=>{});store.close();process.exit(0);});process.on('SIGINT',stop);process.on('SIGTERM',stop);
