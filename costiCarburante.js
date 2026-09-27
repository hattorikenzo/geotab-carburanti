(function(){
"use strict";
const DATA_BASE="https://hattorikenzo.github.io/geotab-carburanti/data";
const GRID=.05, MAX_STATION_METERS=500;
let coverage=null;
let api=null, vehicles=[];

const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const money=n=>new Intl.NumberFormat("it-IT",{style:"currency",currency:"EUR"}).format(n);
const dec=(n,d=2)=>new Intl.NumberFormat("it-IT",{minimumFractionDigits:d,maximumFractionDigits:d}).format(n);



function localYmd(d){let y=d.getFullYear(),m=String(d.getMonth()+1).padStart(2,"0"),day=String(d.getDate()).padStart(2,"0");return `${y}-${m}-${day}`}
function setDefaultDates(){
 let to=new Date(),from=new Date(to);from.setDate(from.getDate()-30);
 $("dateFrom").value=localYmd(from);$("dateTo").value=localYmd(to);
 $("dateTo").max=localYmd(to);
}
function call(method,params){return new Promise((resolve,reject)=>{try{api.call(method,params||{},resolve,e=>reject(new Error(typeof e==="string"?e:JSON.stringify(e))))}catch(e){reject(e)}})}
function get(type,search,resultsLimit){return call("Get",{typeName:type,search:search||{},resultsLimit:resultsLimit||50000})}
function dtLocal(v){let d=new Date(v);return isNaN(d)?String(v):d.toLocaleString("it-IT")}
function isoNoZone(v){let d=new Date(v);if(isNaN(d))return String(v).slice(0,19);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,19)}
function hav(lat1,lon1,lat2,lon2){const R=6371000,p=Math.PI/180,a=Math.sin((lat2-lat1)*p/2)**2+Math.cos(lat1*p)*Math.cos(lat2*p)*Math.sin((lon2-lon1)*p/2)**2;return 2*R*Math.asin(Math.sqrt(a))}
function normalizeFuel(v){let s=String(v||"").toLowerCase();if(/diesel|gasolio/.test(s))return "Gasolio";if(/sp95|gasoline|petrol|benzina/.test(s))return "Benzina";if(/lpg|gpl/.test(s))return "GPL";if(/cng|metano/.test(s))return "Metano";return ""}
function fillCoords(f){let lat=f.latitude,lon=f.longitude;if(f.location){if(lat==null)lat=f.location.y;if(lon==null)lon=f.location.x}lat=Number(lat);lon=Number(lon);return Number.isFinite(lat)&&Number.isFinite(lon)?[lat,lon]:null}
function litersOf(f){for(const k of ["volume","liters","fuelVolume","totalFuel"]){let n=Number(f[k]);if(Number.isFinite(n)&&n>0)return n}return 0}
async function fetchJson(url){let r=await fetch(url,{cache:"no-store"});if(!r.ok)throw new Error("HTTP "+r.status+" "+url);return r.json()}
async function fetchGzip(url){let r=await fetch(url,{cache:"no-store"});if(!r.ok)throw new Error("HTTP "+r.status+" "+url);if(!("DecompressionStream" in window))throw new Error("Browser senza supporto gzip DecompressionStream");let b=await r.blob(),ds=new DecompressionStream("gzip");return JSON.parse(await new Response(b.stream().pipeThrough(ds)).text())}
function quarterOf(v){let d=new Date(v);if(isNaN(d))return "";let y=d.getFullYear(),q=Math.floor(d.getMonth()/3)+1;return `${y}-Q${q}`}
async function loadCoverage(){
 try{
  let [m,a]=await Promise.all([fetchJson(`${DATA_BASE}/manifest.json`).catch(()=>({})),fetchJson(`${DATA_BASE}/archivi_importati.json`).catch(()=>({}))]);
  let imported=Array.isArray(a)?a:[...(a.importati||[]),...(a.trimestri_importati||[])];
  let qs=new Set([...(m.trimestri_mimit||[]),...imported]);
  coverage={manifest:m,quarters:qs};
 }catch(e){coverage={manifest:{},quarters:new Set()}}
}
function quarterIsOfficial(v){return !!(coverage&&coverage.quarters&&coverage.quarters.has(quarterOf(v)))}
async function nearestStation(lat,lon){
 let cy=Math.floor(lat/GRID),cx=Math.floor(lon/GRID),all=[];
 await Promise.all(Array.from({length:9},(_,i)=>{let dy=Math.floor(i/3)-1,dx=i%3-1;return fetchJson(`${DATA_BASE}/geo/${cy+dy}_${cx+dx}.json`).then(a=>all.push(...a)).catch(()=>{})}));
 let best=null;for(const s of all){let d=hav(lat,lon,Number(s[1]),Number(s[2]));if(!best||d<best.distance)best={raw:s,distance:d}}
 return best&&best.distance<=MAX_STATION_METERS?best:null;
}
async function priceFromFile(url,fuel,fillDate,source){
 let o=await fetchGzip(url),keys=Object.keys(o.prezzi||{}),candidates=[];
 for(const key of keys){let [name,self]=key.split("|");if(normalizeFuel(name)!==fuel)continue;for(const r of o.prezzi[key]||[]){if(String(r[0])<=fillDate)candidates.push({date:String(r[0]),price:Number(r[1]),self,source:r[2]||source})}}
 candidates.sort((a,b)=>b.date.localeCompare(a.date));if(!candidates.length)return null;
 let latest=candidates[0],self=candidates.find(x=>x.self==="1"&&x.date===latest.date);return self||latest;
}
async function priceFromDailyFallback(url,fuel,fillDate){
 let o=await fetchGzip(url),arr=(o.giornalieri||{})[fuel]||[];
 let fillDay=String(fillDate).slice(0,10),best=null;
 for(const r of arr){let day=String(r[0]);if(day<=fillDay&&(!best||day>best.date))best={date:day,price:Number(r[1]),source:"MIMIT - snapshot giornaliero"}}
 return best;
}
async function historicalPrice(stationId,fuel,fillDate){
 let bucket=String(Math.floor(stationId/1000)).padStart(3,"0"),mimitUrl=`${DATA_BASE}/prezzi/${bucket}/${stationId}.json.gz`;
 if(quarterIsOfficial(fillDate)){let hp=await priceFromFile(mimitUrl,fuel,fillDate,"MIMIT").catch(()=>null);return hp?{...hp,official:true}:null}
 let hp=await priceFromFile(mimitUrl,fuel,fillDate,"MIMIT giornaliero").catch(()=>null);
 if(hp&&quarterOf(hp.date)===quarterOf(fillDate))return {...hp,official:false};
 let fb=await priceFromDailyFallback(`${DATA_BASE}/fallback_mensile/${bucket}/${stationId}.json.gz`,fuel,fillDate).catch(()=>null);
 if(fb&&quarterOf(fb.date)===quarterOf(fillDate))return {...fb,official:false};
 return null;
}
function supplier(s){let r=s.raw,gestore=r[3]||"",bandiera=r[4]||"",nome=r[6]||"";return {gestore,bandiera,nome,address:[r[7],r[8],r[9]].filter(Boolean).join(" — ")}}
async function fuelForVehicle(v){let f=normalizeFuel(v.fuelType||v.propulsionType||v.powertrainType);if(f)return f;let groups=(v.groups||[]).map(g=>String(g.id||g.name||"")).join(" ");return normalizeFuel(groups)}
async function processFill(f,vehicleFuel){
 let coords=fillCoords(f),liters=litersOf(f),date=f.dateTime||f.date||f.timestamp,dateKey=isoNoZone(date),fuel=normalizeFuel(f.productType)||vehicleFuel,out={date,liters,fuel,coords};
 if(!coords){out.error="Coordinate non disponibili";return out}
 let ns=await nearestStation(coords[0],coords[1]);if(!ns){out.error="Impianto MIMIT non identificato entro "+MAX_STATION_METERS+" m";return out}
 out.station=ns;out.supplier=supplier(ns);if(!fuel){out.error="Tipo carburante non riconosciuto";return out}
 let hp=await historicalPrice(Number(ns.raw[0]),fuel,dateKey);if(!hp){out.error="Prezzo storico non disponibile";return out}
 out.price=hp;out.source=hp.source||"MIMIT";out.cost=liters*hp.price;out.verified=true;return out;
}
function render(items){
 let tb=$("rows");tb.innerHTML="";let tl=0,tc=0,vl=0;
 for(const x of items){
  tl+=x.liters||0;if(x.verified){tc+=x.cost;vl+=x.liters}
  let sup=x.supplier?`<div class="cc-supplier">${esc(x.supplier.gestore||x.supplier.nome||"Impianto MIMIT")}</div><div class="cc-sub">${esc([x.supplier.bandiera,x.supplier.nome].filter(Boolean).join(" — "))}</div><div class="cc-sub">${esc(x.supplier.address)}${x.station?` · ${dec(x.station.distance,1)} m`:""}</div>`:`<span class="cc-warn">${esc(x.error||"Non identificato")}</span>`;
  let price=x.verified?`${dec(x.price.price,3)}<div class="cc-sub">${esc(dtLocal(x.price.date))}</div>`:`<span class="cc-warn">—</span>`,cost=x.verified?money(x.cost):"—",tr=document.createElement("tr");
  tr.innerHTML=`<td>${esc(dtLocal(x.date))}</td><td>${sup}</td><td>${esc(x.fuel||"—")}</td><td class="cc-num">${price}</td><td>${esc(x.source||"—")}</td><td class="cc-num">${dec(x.liters||0,2)} L</td><td class="cc-num"><b>${cost}</b></td>`;tb.appendChild(tr);
 }
 if(!items.length)tb.innerHTML='<tr><td colspan="7" class="cc-muted">Nessun rifornimento nel periodo selezionato.</td></tr>';
 $("n").textContent=items.length;$("liters").textContent=dec(tl,2)+" L";$("total").textContent=money(tc);$("avg").textContent=vl>0?dec(tc/vl,3)+" €/L":"—";
}
async function loadHistory(){
 let vid=$("vehicle").value,fromValue=$("dateFrom").value,toValue=$("dateTo").value;
 if(!vid){$("status").className="status err";$("status").textContent="Seleziona un veicolo.";return}
 if(!fromValue||!toValue){$("status").className="status err";$("status").textContent="Seleziona la data iniziale e la data finale.";return}
 if(fromValue>toValue){$("status").className="status err";$("status").textContent="La data iniziale non può essere successiva alla data finale.";return}
 let from=new Date(`${fromValue}T00:00:00`),to=new Date(`${toValue}T23:59:59.999`);
 $("load").disabled=true;$("status").className="status";$("status").textContent="Caricamento rifornimenti…";
 try{
  let fills=await get("FillUp",{deviceSearch:{id:vid},fromDate:from.toISOString(),toDate:to.toISOString()},50000);
  fills=(fills||[]).sort((a,b)=>new Date(b.dateTime||b.date)-new Date(a.dateTime||a.date));
  let v=vehicles.find(x=>x.id===vid)||{},vf=await fuelForVehicle(v),out=[];
  for(let i=0;i<fills.length;i++){$("status").textContent=`Analisi rifornimento ${i+1} di ${fills.length}…`;try{out.push(await processFill(fills[i],vf))}catch(e){out.push({date:fills[i].dateTime||fills[i].date,liters:litersOf(fills[i]),error:e.message})}}
  render(out);$("status").className="status ok";$("status").textContent=`Completato: ${fills.length} rifornimenti analizzati dal ${from.toLocaleDateString("it-IT")} al ${to.toLocaleDateString("it-IT")}.`;
 }catch(e){$("status").className="status err";$("status").textContent="Errore: "+e.message}
 finally{$("load").disabled=false}
}
async function init(myApi){
 api=myApi;setDefaultDates();$("status").textContent="MyGeotab collegato. Caricamento veicoli…";
 try{
  await loadCoverage();
  vehicles=await get("Device",{},50000);vehicles=(vehicles||[]).filter(v=>!v.isArchived).sort((a,b)=>String(a.name).localeCompare(String(b.name)));
  $("vehicle").innerHTML='<option value="">Seleziona un veicolo…</option>'+vehicles.map(v=>`<option value="${esc(v.id)}">${esc(v.name||v.id)}</option>`).join("");
  $("load").disabled=false;$("status").className="status ok";$("status").textContent=`MyGeotab collegato. ${vehicles.length} veicoli disponibili.`;
 }catch(e){$("status").className="status err";$("status").textContent="Errore MyGeotab: "+e.message}
}
$("load").addEventListener("click",loadHistory);
$("dateFrom").addEventListener("change",()=>{$("dateTo").min=$("dateFrom").value||""});
window.geotab=window.geotab||{};window.geotab.addin=window.geotab.addin||{};
window.geotab.addin.costiCarburante=function(){return {initialize:function(api,state,callback){init(api).finally(()=>callback&&callback())},focus:function(api,state){if(!window.__fuelApi){window.__fuelApi=true;api&&init(api)}},blur:function(){}}};
})();