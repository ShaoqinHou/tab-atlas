import { stableHash } from '../domain/identity.js';
import dns from 'node:dns/promises';
import net from 'node:net';

export class PublicEvidenceService {
  constructor({fetchFn=fetch,lookupFn=dns.lookup,maxBytes=1_500_000,timeoutMs=8000,maxRedirects=3}={}){this.fetchFn=fetchFn;this.lookupFn=lookupFn;this.maxBytes=maxBytes;this.timeoutMs=timeoutMs;this.maxRedirects=maxRedirects;}
  async acquire(resource,{signal}={}){
    const url=new URL(resource.canonical_url); await assertPublicHost(url.hostname,this.lookupFn);
    if(resource.resource_type==='image') return {adapter:'direct-image',kind:'preview',status:'ok',sourceUrl:resource.canonical_url,payload:{title:resource.title,image:resource.canonical_url,readableText:''},limitation:'',contentHash:stableHash(resource.canonical_url)};
    if(resource.resource_type==='video' && isYouTube(url)) return this.#youtube(resource,signal);
    return this.#page(resource,signal);
  }
  async #youtube(resource,signal){
    const endpoint=`https://www.youtube.com/oembed?url=${encodeURIComponent(resource.canonical_url)}&format=json`;
    try{const data=await this.#json(endpoint,signal);return {adapter:'youtube-oembed',kind:'metadata',status:'limited',sourceUrl:endpoint,payload:{title:data.title??resource.title,author:data.author_name??'',thumbnail:await safePreviewUrl(data.thumbnail_url??'',endpoint,this.lookupFn)},limitation:'Public oEmbed metadata available; transcript/captions were not fetched by this adapter.',contentHash:stableHash(data)};}catch(e){return limited(resource,'youtube-oembed',e.message);}
  }
  async #page(resource,signal){
    try{const text=await this.#text(resource.canonical_url,signal);const title=pick(text,/<title[^>]*>([\s\S]*?)<\/title>/i);const desc=pickMeta(text,'description');const rawImage=pickMeta(text,'og:image');const image=await safePreviewUrl(rawImage,resource.canonical_url,this.lookupFn);return {adapter:'public-html',kind:'metadata',status:text?'ok':'limited',sourceUrl:resource.canonical_url,payload:{title:decode(title)||resource.title,description:decode(desc),image,readableText:stripHtml(text).slice(0,4000)},limitation:text?'':'No public HTML content returned.',contentHash:stableHash(text)};}catch(e){return limited(resource,'public-html',e.message);}
  }
  async #text(input,signal){
    const c=new AbortController(), timer=setTimeout(()=>c.abort(),this.timeoutMs), abort=()=>c.abort();signal?.addEventListener('abort',abort,{once:true});
    try{
      let current=new URL(input);
      for(let redirects=0;;redirects++){
        await assertPublicHost(current.hostname,this.lookupFn);
        const r=await this.fetchFn(current,{redirect:'manual',signal:c.signal,headers:{'user-agent':'TabAtlas/0.1 local-library'}});
        if([301,302,303,307,308].includes(r.status)){
          if(redirects>=this.maxRedirects)throw new Error('Too many evidence redirects');
          const location=r.headers.get('location');if(!location)throw new Error('Redirect response had no Location');
          current=new URL(location,current);if(!['http:','https:'].includes(current.protocol))throw new Error('Evidence redirect used an unsupported protocol');continue;
        }
        if(!r.ok)throw new Error(`HTTP ${r.status}`);const len=Number(r.headers.get('content-length')||0);if(len>this.maxBytes)throw new Error('Response exceeds evidence size limit');const b=Buffer.from(await r.arrayBuffer());if(b.length>this.maxBytes)throw new Error('Response exceeds evidence size limit');return b.toString('utf8');
      }
    } finally {clearTimeout(timer);signal?.removeEventListener('abort',abort);}
  }
  async #json(url,signal){return JSON.parse(await this.#text(url,signal));}
}
function limited(r,a,msg){return {adapter:a,kind:'metadata',status:'limited',sourceUrl:r.canonical_url,payload:{title:r.title},limitation:msg,contentHash:stableHash(`${a}:${msg}`)};}
function isYouTube(u){return ['youtube.com','www.youtube.com','youtu.be'].includes(u.hostname);}
async function safePreviewUrl(raw,base,lookupFn){if(!raw)return'';try{const u=new URL(raw,base);if(!['http:','https:'].includes(u.protocol))return'';await assertPublicHost(u.hostname,lookupFn);return u.toString();}catch{return'';}}
function pick(s,re){return re.exec(s)?.[1]?.trim()??'';}function pickMeta(s,name){const esc=name.replace(':','\\:');return pick(s,new RegExp(`<meta[^>]+(?:name|property)=["']${esc}["'][^>]+content=["']([^"']*)["']`,'i'))||pick(s,new RegExp(`<meta[^>]+content=["']([^"']*)["'][^>]+(?:name|property)=["']${esc}["']`,'i'));}
function decode(s){return s.replace(/&amp;/g,'&').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>');}function stripHtml(s){return decode(s.replace(/<script[\s\S]*?<\/script>/gi,' ').replace(/<style[\s\S]*?<\/style>/gi,' ').replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim());}
export async function assertPublicHost(host,lookupFn=dns.lookup){if(host==='localhost'||(net.isIP(host)&&isPrivateIp(host)))throw new Error('Private/local network URLs are not eligible for public evidence fetch');const results=await lookupFn(host,{all:true});if(results.some(x=>isPrivateIp(x.address)))throw new Error('Hostname resolves to a private/local address');}
function isPrivateIp(ip){if(ip.includes(':'))return ip==='::1'||ip.startsWith('fc')||ip.startsWith('fd')||ip.startsWith('fe80:');const p=ip.split('.').map(Number);return p[0]===10||p[0]===127||(p[0]===169&&p[1]===254)||(p[0]===172&&p[1]>=16&&p[1]<=31)||(p[0]===192&&p[1]===168);}
