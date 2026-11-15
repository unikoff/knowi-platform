// Loopback-only built frontend + same-origin proxy to this task's isolated Core.
import http from 'node:http';
import { createReadStream } from 'node:fs';
import { appendFile, mkdir, realpath, stat } from 'node:fs/promises';
import { dirname, extname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const root=resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const args=process.argv.slice(2);
const option=(name,fallback)=>{const i=args.indexOf(name);return i<0?fallback:args[i+1];};
const base=option('--base','/');
const port=Number(option('--port','18519'));
if(!['/','/platform/'].includes(base)||!Number.isInteger(port)||port<1024||port>65535||[8001,18001].includes(port))throw Error('Invalid isolated web address');
const dist=await realpath(resolve(root,option('--dist','apps/web/dist')));
if(!dist.startsWith(root+sep))throw Error('Build must be inside the isolated workspace');
const ledger=resolve(root,option('--ledger','apps/web/.local/logs/static-requests.jsonl'));
if(!ledger.startsWith(root+sep))throw Error('Ledger must be inside the isolated workspace');
await mkdir(dirname(ledger),{recursive:true});
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.woff2':'font/woff2','.mp4':'video/mp4'};
http.createServer(async(req,res)=>{
 const url=new URL(req.url,'http://127.0.0.1');
 const path=url.pathname;let bytes=0;
 const write=res.write.bind(res),end=res.end.bind(res);
 res.write=(chunk,...rest)=>{if(chunk)bytes+=Buffer.byteLength(chunk);return write(chunk,...rest);};
 res.end=(chunk,...rest)=>{if(chunk)bytes+=Buffer.byteLength(chunk);return end(chunk,...rest);};
 res.on('finish',()=>{void appendFile(ledger,JSON.stringify({time:new Date().toISOString(),method:req.method,path,course_id:url.searchParams.get('course_id'),status:res.statusCode,bytes})+'\n');});
 const api=base+'api/';
 if(path.startsWith(api)){
  const target=http.request({hostname:'127.0.0.1',port:18001,path:'/api/'+path.slice(api.length)+url.search,method:req.method,headers:req.headers},upstream=>{res.writeHead(upstream.statusCode,upstream.headers);upstream.pipe(res);});
  target.on('error',()=>{res.writeHead(502,{'Content-Type':'application/json'});res.end('{"detail":"Isolated Core unavailable"}');});
  req.pipe(target);return;
 }
 if(!['GET','HEAD'].includes(req.method)||!path.startsWith(base)){res.writeHead(404);res.end();return;}
 try{
  const local=decodeURIComponent(path.slice(base.length));
  if(local.includes('\0')||local.includes('\\')||local.split('/').some(p=>p==='..'||p==='.'))throw Error('path');
  const entry=/^backend\/demo\/?$/.test(local)?'devtools/lesson-visual-demo.html':local||'index.html';
  let file=resolve(dist,entry);
  let rel=relative(dist,file);
  if(isAbsolute(rel)||rel==='..'||rel.startsWith('..'+sep))throw Error('path');
  let info=await stat(file).catch(()=>null);
  if(!info?.isFile()){
   if(extname(local)){res.writeHead(404);res.end();return;}
   file=resolve(dist,'index.html');info=await stat(file);
  }
  const canonical=await realpath(file);
  if(!canonical.startsWith(dist+sep))throw Error('path');
  res.writeHead(200,{'Content-Type':types[extname(file)]||'application/octet-stream','Cache-Control':'no-store'});
  if(req.method==='HEAD')res.end();else createReadStream(file).pipe(res);
 }catch{res.writeHead(404);res.end();}
}).listen(port,'127.0.0.1',()=>console.log(JSON.stringify({url:`http://127.0.0.1:${port}${base}`,dist,ledger,api:'http://127.0.0.1:18001',external_delivery:false})));
