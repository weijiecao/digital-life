// Run with NODE_PATH pointing to an installed jsdom; validates real UI -> real ConsoleApi.
const {JSDOM,VirtualConsole}=require('jsdom');
const fs=require('fs'),path=require('path'),{spawn}=require('child_process'),readline=require('readline'),assert=require('assert');
const [python,packageDir]=process.argv.slice(2);
const proc=spawn(python,[path.join(__dirname,'console_rpc_fixture.py'),packageDir],{stdio:['pipe','pipe','inherit'],env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'}});
const pending=new Map();let serial=0,readyResolve;const ready=new Promise(r=>readyResolve=r);
readline.createInterface({input:proc.stdout}).on('line',line=>{const r=JSON.parse(line);if(!r.id)return readyResolve(r);const p=pending.get(r.id);pending.delete(r.id);if(r.error||r.data?.error)p.reject(new Error(r.error||r.data.error));else p.resolve(r.data);});
function rpc(endpoint,body,query){return new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});proc.stdin.write(JSON.stringify({id,endpoint,body,query})+'\n');});}
async function main(){
 const info=await ready;const errors=[];const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const html=fs.readFileSync(path.join(packageDir,'views/console/index.html'),'utf8');
 const dom=new JSDOM(html,{url:'http://localhost/#unified',runScripts:'outside-only',virtualConsole:vc});const w=dom.window;
 w.AstrBotPluginPage={apiGet:(e,q)=>rpc(e,undefined,q),apiPost:(e,b)=>rpc(e,b)};
 w.eval(fs.readFileSync(path.join(packageDir,'views/console/app.js'),'utf8').replace(/boot\(\);\s*$/,''));
 w.eval('confirmDialog=async()=>({ok:true});');
 await w.eval('renderUnified()');
 const doc=w.document;
 doc.querySelector('#migration-source').value=info.source;
 doc.querySelector('#migration-map').value=JSON.stringify({'old-a':info.origin});
 async function click(action){const el=doc.querySelector(`[data-act="${action}"]`);assert(el&&!el.disabled,action+' unavailable');w.__button=el;await w.eval('handleActionClick({target:__button})');const error=doc.querySelector('#toast.err');assert(!error,error?.textContent);}
 await click('unified-create');await click('unified-batch');assert(doc.querySelector('#view').textContent.includes('READY'));
 await click('unified-release');assert(doc.querySelector('#view').textContent.includes('RELEASED'));
 await click('unified-backup');await click('unified-verify');assert(doc.querySelector('#toast').textContent.includes('校验通过'));
 await click('unified-restore');assert.equal(doc.querySelectorAll('[data-act="unified-restore"]').length,2);
 doc.querySelector('#withdraw-scope').value=info.scope;doc.querySelector('#withdraw-seq').value='1';
 // 在确认框打开后，由另一个管理会话恢复同一备份。
 let confirmOpened, resumeConfirm;
 const opened=new Promise(resolve=>{confirmOpened=resolve;});
 w.waitForConfirmation=()=>{confirmOpened();return new Promise(resolve=>{resumeConfirm=resolve;});};
 w.eval('confirmDialog=waitForConfirmation;');
 w.__button=doc.querySelector('[data-act="unified-withdraw"]');
 const pendingWithdraw=w.eval('handleActionClick({target:__button})');
 await opened;
 const backupId=doc.querySelector('[data-act="unified-restore"]').dataset.id;
 await rpc('console/runtime/restore',{id:backupId});
 resumeConfirm({ok:true});
 await pendingWithdraw;
 assert(doc.querySelector('#toast').textContent.includes('代次'));
 assert(!(await rpc('console/runtime/status')).inbox.WITHDRAWN);
 w.eval('confirmDialog=async()=>({ok:true});');
 await w.eval('renderUnified()');
 doc.querySelector('#withdraw-scope').value=info.scope;doc.querySelector('#withdraw-seq').value='1';
 await click('unified-withdraw');assert(doc.querySelector('#toast').textContent.includes('已撤回'));
 const state=await rpc('console/runtime/status');assert.equal(state.inbox.WITHDRAWN,1);assert.equal(state.store_generation,3);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({environment:'jsdom + actual packaged ConsoleApi/ApplicationService; AstrBot stub',operations:['migration_create','migration_batch','migration_release','backup','verify_backup','restore','stale_withdraw_rejected','withdraw'],inbox:state.inbox,generation:state.store_generation,dom_errors:errors,visual_browser_validation:false},null,2));
 dom.window.close();proc.stdin.end();
}
main().catch(e=>{console.error(e);proc.stdin.end();process.exitCode=1;});
