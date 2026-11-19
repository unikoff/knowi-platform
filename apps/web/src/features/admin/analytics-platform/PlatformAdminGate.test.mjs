import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import ts from "typescript";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";

const source = fs.readFileSync(new URL("../PlatformAdmin.tsx", import.meta.url), "utf8");
function renderPlatform(flag) {
  let analyticsMounts = 0;
  const transformed = source.replaceAll("import.meta.env.VITE_ANALYTICS_REPORTS_ENABLED", JSON.stringify(flag)).replaceAll("import.meta.env.BASE_URL", JSON.stringify("/"));
  const code = ts.transpileModule(transformed, {compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
  const noop = () => null;
  const modules = {
    "react": React,
    "react/jsx-runtime": awaitableJsxRuntime,
    "../auth/useAuthSession": {useAuthSession: () => ({user: {id:1, role:"admin", capabilities:["platform.read"], account_ref:"actor", auth_revision:1}, checking:false, initialized:true, recheck(){}, revision:1})},
    "../auth": {AuthScreen:noop}, "../../app/StartupScreen": {StartupScreen:noop},
    "../../shared/course-paths": {courseUrls:()=>({learning:"/backend"}), isCourseId:()=>true, normalizedBase:()=>"/"},
    "./scopedApi": {acknowledgeReconciliation(){}, ReconciliationRequired: class extends Error {}, command(){}, read(){}},
    "../../shared/lib/moscow-month": {moscowCalendarMonth:()=>({year:2026,month:11})},
    "./OperationRow": {OperationRow:noop}, "./PlatformActivity": {PlatformActivity:()=>React.createElement("p",null,"Существующая активность Core")},
    "./analytics-platform/PlatformAnalytics": {PlatformAnalytics:()=>{analyticsMounts++; return React.createElement("p",null,"Analytics mounted");}},
    "./overview/AdminOverviewDashboard": {AdminOverviewDashboard:noop},
    "../../shared/useCourseTitles": {useCourseTitles:()=>({})},
    "./useScopedResource": {useScopedResource:(path)=>({loading:false,error:"",data:path?.startsWith("/admin-platform/overview") ? {users:7,active_subscribers:2,courses:1,dashboard:null} : path?.startsWith("/admin-platform/courses") ? {courses:[],next_cursor:null} : null})},
    "./moscowDay": {watchMoscowDay:()=>()=>{}},
    "./admin.css":{}, "./admin-scoped.css":{}, "./platform-admin.css":{},
  };
  const require = id => {if (!(id in modules)) throw Error(`Unexpected dependency ${id}`); return modules[id];};
  const output={exports:{}};new Function("require","module","exports",code)(require,output,output.exports);
  const html=renderToStaticMarkup(React.createElement(output.exports.PlatformAdminEntry));
  return {html,analyticsMounts};
}
const awaitableJsxRuntime = await import("react/jsx-runtime");

test("flag OFF preserves Core overview and does not mount the Analytics requester",()=>{
  for (const flag of [undefined,"false", "TRUE", ""]) {
    const {html,analyticsMounts}=renderPlatform(flag);
    assert.equal(analyticsMounts,0);assert.match(html,/Существующая активность Core/);assert.match(html,/Вся платформа/);assert.doesNotMatch(html,/Analytics mounted/);
  }
});
test("only flag true mounts the Analytics requester alongside the Core overview",()=>{
  const {html,analyticsMounts}=renderPlatform("true");
  assert.equal(analyticsMounts,1);assert.match(html,/Analytics mounted/);assert.match(html,/Существующая активность Core/);
});

function createPlatformHarness({ resource = () => null, scopedApi = {}, confirm = () => true } = {}) {
  const transformed=source.replaceAll("import.meta.env.VITE_ANALYTICS_REPORTS_ENABLED",JSON.stringify("false")).replaceAll("import.meta.env.BASE_URL",JSON.stringify("/"));
  const code=ts.transpileModule(transformed,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
  const slots=[],effects=[];let cursor=0,queued=[],refresh,stopped=false;const resources=[];
  const hooks={
    useState(initial){const index=cursor++;if(!(index in slots))slots[index]=typeof initial==="function"?initial():initial;return [slots[index],value=>{slots[index]=typeof value==="function"?value(slots[index]):value;}];},
    useRef(initial){const index=cursor++;if(!(index in slots))slots[index]={current:initial};return slots[index];},
    useEffect(callback,deps){const index=cursor++,old=effects[index];if(!old||deps.some((value,i)=>!Object.is(value,old.deps[i])))queued.push({index,callback,deps});},
  };
  const jsx=(type,props)=>({type,props:props??{}}), noop=()=>null;
  const modules={"react":hooks,"react/jsx-runtime":{jsx,jsxs:jsx,Fragment:Symbol("Fragment")},
    "../auth/useAuthSession":{useAuthSession:()=>({user:{id:1,role:"admin",capabilities:["platform.read"],account_ref:"actor",auth_revision:1},checking:false,initialized:true,revision:1})},
    "../auth":{AuthScreen:noop},"../../app/StartupScreen":{StartupScreen:noop},"../../shared/course-paths":{courseUrls:()=>({learning:"/"}),isCourseId:()=>true,normalizedBase:()=>"/"},
    "./scopedApi":{acknowledgeReconciliation:noop,ReconciliationRequired:class extends Error{},command:noop,read:noop,...scopedApi},"../../shared/lib/moscow-month":{moscowCalendarMonth:()=>({year:2026,month:11})},
    "./OperationRow":{OperationRow:noop},"./PlatformActivity":{PlatformActivity:noop},"./analytics-platform/PlatformAnalytics":{PlatformAnalytics:noop},"./overview/AdminOverviewDashboard":{AdminOverviewDashboard:noop},"../../shared/useCourseTitles":{useCourseTitles:()=>({})},
    "./useScopedResource":{useScopedResource:(path,revision)=>{resources.push({path,revision});return {data:resource(path),loading:false,error:""};}},
    "./moscowDay":{watchMoscowDay:callback=>{refresh=callback;return ()=>{stopped=true;};}},"./admin.css":{},"./admin-scoped.css":{},"./platform-admin.css":{}};
  const require=id=>{if(!(id in modules))throw Error(`Unexpected dependency ${id}`);return modules[id];};
  const output={exports:{}};new Function("require","module","exports","window",code)(require,output,output.exports,{confirm});
  const entry=output.exports.PlatformAdminEntry();
  function render(){cursor=0;queued=[];resources.length=0;const tree=entry.type(entry.props);for(const item of queued){effects[item.index]?.cleanup?.();effects[item.index]={deps:item.deps,cleanup:item.callback()};}return tree;}
  function cleanup(){for(const effect of effects)effect?.cleanup?.();return stopped;}
  return {render, resources, refreshDay: () => refresh(), cleanup};
}

test("day watcher refreshes only the overview resource",()=>{
  const {render, resources, refreshDay, cleanup} = createPlatformHarness();
  render();assert.equal(resources[0].revision,0);assert.equal(resources[1].revision,0);
  refreshDay();render();assert.equal(resources[0].revision,1);assert.equal(resources[1].revision,0);
  assert.equal(cleanup(),true);
});

function findElement(node, matches) {
  if (Array.isArray(node)) return node.map(child => findElement(child, matches)).find(Boolean);
  if (!node?.props) return undefined;
  return matches(node) ? node : findElement(node.props.children, matches);
}

test("platform subscription actions keep both account references and require explicit reconciliation or reset confirmation", async () => {
  const person = {id: 7, account_ref: "target", username: "Ученик", email: "student@example.test", role: "user", subscription: {subscription_until: null}, quotas: []};
  const calls = [];
  const reconciled = [];
  const prompts = [];
  let confirmed = false;
  class ReconciliationRequired extends Error {}
  const pending = new ReconciliationRequired("Исход предыдущего действия неизвестен.");
  const {render} = createPlatformHarness({
    resource: path => path?.startsWith("/admin-platform/users?") ? {users: [person]} : null,
    scopedApi: {
      ReconciliationRequired,
      acknowledgeReconciliation: error => reconciled.push(error),
      command: async (...args) => { calls.push(args); if (calls.length === 1) throw pending; return {}; },
      read: async () => person,
    },
    confirm: message => {prompts.push(message); return confirmed;},
  });
  const button = (tree, label) => findElement(tree, node => node.type === "button" && node.props.children === label);
  button(render(), "Пользователи").props.onClick();
  findElement(render(), node => node.props.className === "admin-account-card admin-scoped-user").props.onClick();
  findElement(render(), node => node.type === "input" && node.props.maxLength === 500).props.onChange({target: {value: "Причина"}});

  button(render(), "Продлить общую подписку").props.onClick();
  assert.deepEqual(calls, [["/admin-platform/users/7/subscription-extensions", {days: 30, reason: "Причина"}, "target", "actor", "admin:1"]]);
  await Promise.resolve();
  button(render(), "Подтвердить, что результат сверен").props.onClick();
  assert.deepEqual(reconciled, []);
  confirmed = true;
  button(render(), "Подтвердить, что результат сверен").props.onClick();
  assert.deepEqual(reconciled, [pending]);
  assert.equal(calls.length, 1);

  confirmed = false;
  button(render(), "Снять общую подписку").props.onClick();
  assert.equal(calls.length, 1);
  confirmed = true;
  button(render(), "Снять общую подписку").props.onClick();
  assert.deepEqual(calls[1], ["/admin-platform/users/7/subscription-reset", {reason: "Причина", confirmed: true}, "target", "actor", "admin:1"]);
  assert.match(prompts[0], /сверили результат/);
  assert.match(prompts[2], /Снять общую подписку/);
});
