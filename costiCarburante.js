 (function(){
    "use strict";
    const DATA_BASE="https://hattorikenzo.github.io/geotab-carburanti/data";
    const GRID=.05, MAX_STATION_METERS=500;
    let coverage=null;
    let api=null, vehicles=[];
    let lastItems=[], lastVehicle=null, vehiclePhotoUrl="";

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

    const FUEL_DIAG_ID="DiagnosticFuelLevelId";
    const DIAG_BEFORE_HOURS=12;
    const DIAG_AFTER_HOURS=42;

    async function fuelDiagnosticForFill(f,deviceId){
     let originalDate=f.dateTime||f.date||f.timestamp,base=new Date(originalDate);
     let result={ok:false,originalDate,samples:[],events:[],message:""};
     if(!deviceId||isNaN(base)){result.message="Data o veicolo non valido";return result}
     let from=new Date(base.getTime()-DIAG_BEFORE_HOURS*3600000);
     let to=new Date(base.getTime()+DIAG_AFTER_HOURS*3600000);
     try{
      // Tentativo 1: ricerca esattamente come il Profilo dati del motore.
      let rows=await get("StatusData",{
       deviceSearch:{id:deviceId},
       diagnosticSearch:{id:FUEL_DIAG_ID},
       fromDate:from.toISOString(),
       toDate:to.toISOString()
      },50000);

      result.rawCount=(rows||[]).length;
      result.samples=(rows||[]).map(r=>({
       id:r.id||"",
       date:r.dateTime,
       level:Number(r.data),
       diagnosticId:r.diagnostic&&r.diagnostic.id?r.diagnostic.id:""
      })).filter(r=>r.date&&Number.isFinite(r.level))
         .sort((a,b)=>new Date(a.date)-new Date(b.date));

      // Non filtriamo gli interpolati in diagnostica: vogliamo vedere esattamente cosa arriva.
      for(let i=1;i<result.samples.length;i++){
       let a=result.samples[i-1],b=result.samples[i],rise=b.level-a.level;
       if(rise>=5)result.events.push({date:b.date,before:a.level,after:b.level,rise});
      }

      result.ok=result.samples.length>0;
      if(!result.ok)result.message="StatusData non ha restituito campioni DiagnosticFuelLevelId.";
      return result;
     }catch(e){
      result.message="Errore StatusData: "+(e&&e.message?e.message:String(e));
      return result;
     }
    }

    async function gpsDiagnostic(deviceId,dateValue){
     let d=new Date(dateValue);
     if(!deviceId||isNaN(d))return null;
     try{
      // Finestra più ampia: il salto Fuel Level può essere registrato pochi minuti
      // prima/dopo la posizione effettiva alla pompa.
      let rows=await get("LogRecord",{
       deviceSearch:{id:deviceId},
       fromDate:new Date(d.getTime()-45*60000).toISOString(),
       toDate:new Date(d.getTime()+45*60000).toISOString()
      },50000);

      let candidates=[];
      for(const r of rows||[]){
       let rd=new Date(r.dateTime),lat=Number(r.latitude),lon=Number(r.longitude),speed=Number(r.speed);
       if(isNaN(rd)||!Number.isFinite(lat)||!Number.isFinite(lon))continue;
       candidates.push({
        date:r.dateTime,dt:rd,lat,lon,
        speed:Number.isFinite(speed)?speed:null,
        delta:Math.abs(rd-d)
       });
      }
      if(!candidates.length)return null;

      // Prima prova tutti i punti GPS nella finestra e privilegia quelli fermi/lenti.
      // nearestStation resta a 500 m: non allarghiamo artificialmente il raggio MIMIT.
      let tested=[];
      for(const c of candidates){
       if(c.delta>45*60000)continue;
       let station=await nearestStation(c.lat,c.lon);
       if(station){
        let stopped=c.speed===null||c.speed<=5;
        tested.push({...c,station,score:c.delta+(stopped?0:20*60000)});
       }
      }
      if(tested.length){
       tested.sort((x,y)=>x.score-y.score||x.station.distance-y.station.distance);
       let best=tested[0];
       return {...best,foundStation:true};
      }

      // Se nessun punto della finestra trova MIMIT, restituiamo comunque
      // il GPS temporalmente più vicino per la diagnostica.
      candidates.sort((x,y)=>x.delta-y.delta);
      return {...candidates[0],foundStation:false};
     }catch(e){return {error:e&&e.message?e.message:String(e)}}
    }
    async function enrichFuelDiagnostic(f,deviceId){
     let d=await fuelDiagnosticForFill(f,deviceId);
     if(d.events.length){
      // Solo diagnostica: NON modifica ancora data, litri, posizione o distributore.
      // Mostriamo il primo aumento successivo al FillUp e la posizione GPS corrispondente.
      let base=new Date(d.originalDate);
      let after=d.events.filter(e=>new Date(e.date)>=base).sort((a,b)=>new Date(a.date)-new Date(b.date));
      let chosen=after[0]||d.events[0];
      d.chosen=chosen;
      d.gps=await gpsDiagnostic(deviceId,chosen.date);
      if(d.gps&&!d.gps.error){
       d.station=d.gps.station||await nearestStation(d.gps.lat,d.gps.lon);
      }
     }
     return d;
    }

    async function processFill(f,vehicleFuel,deviceId,fuelDiag){
     let liters=litersOf(f);
     let originalDate=f.dateTime||f.date||f.timestamp;
     let originalCoords=fillCoords(f);
     let fuel=normalizeFuel(f.productType)||vehicleFuel;

     // Default: dati originali FillUp.
     let date=originalDate,coords=originalCoords,station=null,corrected=false;

     // Se il Profilo dati del motore ha trovato un aumento reale e abbiamo un GPS,
     // usiamo QUEL momento/posizione per identificare il distributore.
     // I litri restano sempre quelli del FillUp Geotab.
     if(fuelDiag&&fuelDiag.chosen&&fuelDiag.gps&&!fuelDiag.gps.error){
      date=fuelDiag.chosen.date;
      coords=[fuelDiag.gps.lat,fuelDiag.gps.lon];
      station=fuelDiag.station||fuelDiag.gps.station||null;
      corrected=true;
     }

     let out={
      date,originalDate,liters,fuel,coords,originalCoords,
      corrected,fuelDiag
     };

     if(!coords){out.error="Coordinate non disponibili";return out}

     // Se la scansione GPS non ha già trovato la stazione, prova sul punto scelto.
     if(!station)station=await nearestStation(coords[0],coords[1]);

     // Sicurezza: se la correlazione Fuel Level non trova alcun MIMIT, prova anche
     // la posizione originale. Non perdiamo quindi rifornimenti già corretti.
     if(!station&&corrected&&originalCoords){
      let originalStation=await nearestStation(originalCoords[0],originalCoords[1]);
      if(originalStation){
       station=originalStation;
       date=originalDate;
       coords=originalCoords;
       out.corrected=false;
       out.date=date;
       out.coords=coords;
      }
     }

     if(!station){out.error="Impianto MIMIT non identificato entro "+MAX_STATION_METERS+" m";return out}

     out.station=station;
     out.supplier=supplier(station);
     if(!fuel){out.error="Tipo carburante non riconosciuto";return out}

     let hp=await historicalPrice(Number(station.raw[0]),fuel,isoNoZone(date));
     if(!hp){out.error="Prezzo storico non disponibile";return out}

     out.price=hp;
     out.source=hp.source||"MIMIT";
     out.cost=liters*hp.price;
     out.verified=true;
     return out;
    }
    function render(items){
     let tb=$("rows");tb.innerHTML="";let tl=0,tc=0,vl=0;
     for(const x of items){
      tl+=x.liters||0;if(x.verified){tc+=x.cost;vl+=x.liters}
      let sup=x.supplier?`<div class="cc-supplier">${esc(x.supplier.gestore||x.supplier.nome||"Impianto MIMIT")}</div><div class="cc-sub">${esc([x.supplier.bandiera,x.supplier.nome].filter(Boolean).join(" — "))}</div><div class="cc-sub">${esc(x.supplier.address)}${x.station?` · ${dec(x.station.distance,1)} m`:""}</div>`:`<span class="cc-warn">${esc(x.error||"Non identificato")}</span>`;
      let price=x.verified?`${dec(x.price.price,3)}<div class="cc-sub">${esc(dtLocal(x.price.date))}</div>`:`<span class="cc-warn">—</span>`,cost=x.verified?money(x.cost):"—",tr=document.createElement("tr");
      tr.innerHTML=`<td>${esc(dtLocal(x.date))}</td><td>${sup}</td><td>${esc(x.fuel||"—")}</td><td class="cc-num">${price}</td><td>${esc(x.source||"—")}</td><td class="cc-num">${dec(x.liters||0,2)} L</td><td class="cc-num"><b>${cost}</b></td>`;tb.appendChild(tr);
      if(x.fuelDiag){
       let d=x.fuelDiag,dr=document.createElement("tr");
       let samplePreview=(d.samples||[]).slice(0,3).map(s=>`${dtLocal(s.date)} = ${dec(s.level,1)}%${s.id?"":" (interpolato)"}`).join(" | ");
       let eventText=(d.events||[]).length?(d.events||[]).slice(0,5).map(e=>`${dtLocal(e.date)}: ${dec(e.before,1)}% → ${dec(e.after,1)}% (+${dec(e.rise,1)}%)`).join(" | "):"nessun aumento ≥ 5%";
       let gps=d.gps&&!d.gps.error?`${dec(d.gps.lat,6)}, ${dec(d.gps.lon,6)} @ ${dtLocal(d.gps.date)}`:(d.gps&&d.gps.error?d.gps.error:"—");
       let station=d.station?`${supplier(d.station).gestore||supplier(d.station).nome||"Impianto MIMIT"} · ${dec(d.station.distance,1)} m`:"—";
       let applied=x.corrected?"SÌ":"NO";
       dr.innerHTML=`<td colspan="7" style="background:#fff8dc;padding:8px 10px;font-size:12px;line-height:1.55"><b>DIAGNOSTICA LIVELLO CARBURANTE</b> · campioni: ${d.rawCount||0}<br><b>Primi campioni:</b> ${esc(samplePreview||"nessuno")}<br><b>Aumenti:</b> ${esc(eventText)}<br><b>Evento scelto:</b> ${d.chosen?esc(dtLocal(d.chosen.date)):"—"} · <b>GPS:</b> ${esc(gps)} · <b>MIMIT:</b> ${esc(station)} · <b>Correzione applicata:</b> ${applied}${d.message?`<br><b>Messaggio:</b> ${esc(d.message)}`:""}</td>`;
       tb.appendChild(dr);
      }
     }
     if(!items.length)tb.innerHTML='<tr><td colspan="7" class="cc-muted">Nessun rifornimento nel periodo selezionato.</td></tr>';
     $("n").textContent=items.length;$("liters").textContent=dec(tl,2)+" L";$("total").textContent=money(tc);$("avg").textContent=vl>0?dec(tc/vl,3)+" €/L":"—";
    }

    function vinOf(v){return String(v.vehicleIdentificationNumber||v.vin||v.serialNumber||"").trim().toUpperCase()}

    function cleanVehicleField(v){return String(v==null?"":v).replace(/\s+/g," ").trim()}

    function firstVehicleField(v,names){
     if(!v)return "";
     for(const n of names){let x=cleanVehicleField(v[n]);if(x)return x}
     return "";
    }

    function geotabVehicleInfo(v){
     if(!v)return null;
     let make=firstVehicleField(v,["make","manufacturer","vehicleMake","assetMake","makeName","manufacturerName"]);
     let model=firstVehicleField(v,["model","vehicleModel","assetModel","modelName"]);
     let year=firstVehicleField(v,["year","modelYear","vehicleYear","assetYear"]);
     let nested=[v.vehicleData,v.minedVehicleData,v.vehicleIdentification,v.vehicleInformation,v.assetInformation,v.identification,v.details,v.metadata].filter(Boolean);
     for(const x of nested){
      if(!make)make=firstVehicleField(x,["make","manufacturer","vehicleMake","makeName","manufacturerName"]);
      if(!model)model=firstVehicleField(x,["model","vehicleModel","modelName"]);
      if(!year)year=firstVehicleField(x,["year","modelYear","vehicleYear"]);
     }
     if(make&&model)return {make,model,year,source:"Geotab",query:[make,model,year,"car"].filter(Boolean).join(" ")};
     return null;
    }

    function knownVehicleFromVin(v){
     let vin=vinOf(v);
     if(!vin)return null;
     if(vin.startsWith("VR3USHNKK"))return {make:"Peugeot",model:"2008",year:"",source:"VIN locale",query:"Peugeot 2008 car"};
     if(vin.startsWith("UU15SDAG35"))return {make:"Dacia",model:"Sandero",year:"",source:"VIN locale",query:"Dacia Sandero car"};
     if(vin==="WAUZZZGA6RA002632")return {make:"Audi",model:"Q2",year:"2024",source:"VIN locale",query:"Audi Q2 2024 car"};
     return null;
    }

    function vehicleFromName(v){
     let name=cleanVehicleField(v&&v.name);
     if(!name)return null;
     const makes=["Abarth","Alfa Romeo","Audi","BMW","Citroen","Citroën","Cupra","Dacia","Fiat","Ford","Honda","Hyundai","Iveco","Jeep","Kia","Land Rover","Lancia","MAN","Mazda","Mercedes-Benz","Mercedes","Mini","Nissan","Opel","Peugeot","Renault","Seat","Skoda","Škoda","Suzuki","Tesla","Toyota","Volkswagen","Volvo"];
     let foundMake="",matchIndex=-1,matchLength=0;
     for(const make of makes){
      let pattern=make.replace(/[.*+?^${}()|[\]\\]/g,"\\$&");
      if(make==="Citroën")pattern="Citro[eë]n";
      if(make==="Škoda")pattern="[ŠS]koda";
      let re=new RegExp("\\b"+pattern+"\\b","i"),m=name.match(re);
      if(m){foundMake=make;matchIndex=m.index;matchLength=m[0].length;break}
     }
     if(!foundMake||matchIndex<0)return null;
     let after=name.slice(matchIndex+matchLength).trim().replace(/^[-–—_:]+/,"").trim();
     if(!after)return null;
     let pieces=after.split(/\s+/),modelParts=[];
     for(const p of pieces){
      if(/^[A-HJ-NPR-Z0-9]{17}$/i.test(p))break;
      if(/^[A-Z]{2}[0-9]{3}[A-Z]{2}$/i.test(p))break;
      modelParts.push(p);
      if(modelParts.length>=3)break;
     }
     let model=modelParts.join(" ").trim();
     if(!model)return null;
     if(/^Mercedes$/i.test(foundMake))foundMake="Mercedes-Benz";
     if(/^Citroën$/i.test(foundMake))foundMake="Citroen";
     if(/^Škoda$/i.test(foundMake))foundMake="Skoda";
     return {make:foundMake,model,year:"",source:"Nome Geotab",query:`${foundMake} ${model} car`};
    }

    async function decodeVin(v){
     let vin=vinOf(v);
     if(!vin)return null;
     let known=knownVehicleFromVin(v);
     if(known)return known;
     try{
      let url="https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/"+encodeURIComponent(vin)+"?format=json";
      let response=await fetch(url,{cache:"force-cache"});
      if(response.ok){
       let json=await response.json(),x=(json.Results||[])[0]||{};
       let make=cleanVehicleField(x.Make),model=cleanVehicleField(x.Model),year=cleanVehicleField(x.ModelYear);
       if(make&&model)return {make,model,year,source:"VIN",query:[make,model,year,"car"].filter(Boolean).join(" ")};
      }
     }catch(e){console.warn("Decoder VIN non disponibile:",e)}
     return null;
    }

    function normalizePhotoWords(v){
     return cleanVehicleField(v).toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g,"").replace(/[^a-z0-9]+/g," ").trim();
    }

    function photoScore(title,info){
     let t=normalizePhotoWords(title),make=normalizePhotoWords(info.make),model=normalizePhotoWords(info.model),year=normalizePhotoWords(info.year),score=0;
     if(make&&t.includes(make))score+=5;
     if(model&&t.includes(model))score+=10;
     if(year&&t.includes(year))score+=2;
     const badWords=["logo","badge","emblem","interior","dashboard","engine","motor","wheel","rim","brochure","diagram","drawing","sketch","toy","miniature"];
     for(const word of badWords)if(t.includes(word))score-=5;
     return score;
    }

    async function searchCommons(query,info){
     try{
      let p=new URLSearchParams({action:"query",generator:"search",gsrsearch:`${query} filetype:bitmap`,gsrnamespace:"6",gsrlimit:"20",prop:"imageinfo",iiprop:"url",iiurlwidth:"600",format:"json",origin:"*"});
      let response=await fetch("https://commons.wikimedia.org/w/api.php?"+p.toString(),{cache:"force-cache"});
      if(!response.ok)return null;
      let json=await response.json(),pages=Object.values((json.query||{}).pages||{}),candidates=[];
      for(const page of pages){
       let imageInfo=(page.imageinfo||[])[0];
       if(!imageInfo)continue;
       let url=imageInfo.thumburl||imageInfo.url;
       if(!url)continue;
       candidates.push({url,title:page.title||"",score:photoScore(page.title||"",info)});
      }
      candidates.sort((a,b)=>b.score-a.score);
      if(!candidates.length||candidates[0].score<10)return null;
      return candidates[0].url;
     }catch(e){console.warn("Ricerca Wikimedia fallita:",e);return null}
    }

    async function commonsPhoto(info){
     if(!info||!info.make||!info.model)return null;
     let q1=[info.make,info.model,info.year,"car"].filter(Boolean).join(" ");
     let photo=await searchCommons(q1,info);
     if(photo)return photo;
     let q2=[info.make,info.model,"automobile"].filter(Boolean).join(" ");
     photo=await searchCommons(q2,info);
     return photo||null;
    }

    function setPhoto(url){
     vehiclePhotoUrl=url||"";
     $("vehiclePhoto").innerHTML=url?`<img src="${esc(url)}" alt="Foto generica del veicolo">`:'<div class="cc-photo-placeholder">🚗</div>';
     let img=$("vehiclePhoto").querySelector("img");
     if(img)img.addEventListener("error",()=>setPhoto(""),{once:true});
    }

    async function updateVehiclePhoto(){
     let v=vehicles.find(x=>x.id===$("vehicle").value);
     lastVehicle=v||null;setPhoto("");
     if(!v)return;
     let info=geotabVehicleInfo(v);
     if(!info)info=await decodeVin(v);
     if(!info)info=vehicleFromName(v);
     if(!info){
      console.warn("Veicolo non identificato:",{name:v.name,vin:vinOf(v)});
      return;
     }
     console.log("Veicolo identificato:",{name:v.name,vin:vinOf(v),make:info.make,model:info.model,year:info.year,source:info.source});
     let url=await commonsPhoto(info);
     if(!url)console.warn("Foto non trovata:",info);
     setPhoto(url);
    }

    function exportEnabled(on){for(const id of ["pdf","excel","print"])$(id).disabled=!on}
    function reportRows(){
     return lastItems.map(x=>({
      data:dtLocal(x.date),
      distributore:x.supplier?(x.supplier.gestore||x.supplier.nome||"Impianto MIMIT"):(x.error||"Non identificato"),
      carburante:x.fuel||"",
      prezzo:x.verified?Number(x.price.price).toFixed(3):"",
      fonte:x.source||"",
      litri:Number(x.liters||0).toFixed(2),
      costo:x.verified?Number(x.cost).toFixed(2):""
     }));
    }
    function downloadBlob(blob,name){let a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=name;document.body.appendChild(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},1000)}
    function exportExcel(){
     let rows=reportRows(),v=lastVehicle||{},vin=vinOf(v),from=$("dateFrom").value,to=$("dateTo").value;
     let xml='<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="Costi Carburante"><Table>';
     let row=a=>'<Row>'+a.map(c=>'<Cell><Data ss:Type="String">'+esc(c)+'</Data></Cell>').join("")+'</Row>';
     xml+=row(["Costi Carburante"])+row(["Veicolo",v.name||""])+row(["VIN",vin])+row(["Periodo",from+" - "+to])+row([]);
     xml+=row(["Data rifornimento","Distributore rilevato","Carburante","Prezzo €/L","Fonte","Litri","Costo totale €"]);
     for(const r of rows)xml+=row([r.data,r.distributore,r.carburante,r.prezzo,r.fonte,r.litri,r.costo]);
     xml+="</Table></Worksheet></Workbook>";
     downloadBlob(new Blob([xml],{type:"application/vnd.ms-excel;charset=utf-8"}),`costi-carburante-${from}-${to}.xls`);
    }
    function pdfEscape(s){return String(s??"").replace(/[^\x20-\x7E\xA0-\xFF]/g," ").replace(/\\/g,"\\\\").replace(/\(/g,"\\(").replace(/\)/g,"\\)")}
    function exportPdf(){
     let rows=reportRows(),v=lastVehicle||{},from=$("dateFrom").value,to=$("dateTo").value;
     let lines=[`COSTI CARBURANTE`,`Veicolo: ${v.name||""}`,`Periodo: ${from} - ${to}`,""];
     lines.push("Data | Distributore | Carburante | Prezzo | Fonte | Litri | Costo");
     for(const r of rows)lines.push(`${r.data} | ${r.distributore} | ${r.carburante} | ${r.prezzo} | ${r.fonte} | ${r.litri} | ${r.costo}`);
     let pages=[];for(let i=0;i<lines.length;i+=42)pages.push(lines.slice(i,i+42));
     let objs=[],pageIds=[],fontId=3, next=4;
     for(let p=0;p<pages.length;p++){pageIds.push(next);next+=2}
     objs[1]="<< /Type /Catalog /Pages 2 0 R >>";
     objs[2]=`<< /Type /Pages /Kids [${pageIds.map(x=>x+" 0 R").join(" ")}] /Count ${pages.length} >>`;
     objs[3]="<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>";
     for(let p=0;p<pages.length;p++){
      let pid=pageIds[p],cid=pid+1,y=800,content="BT /F1 9 Tf ";
      for(const line of pages[p]){content+=`1 0 0 1 35 ${y} Tm (${pdfEscape(line).slice(0,155)}) Tj `;y-=18}
      content+="ET";
      objs[pid]=`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 ${fontId} 0 R >> >> /Contents ${cid} 0 R >>`;
      objs[cid]=`<< /Length ${content.length} >>\nstream\n${content}\nendstream`;
     }
     let pdf="%PDF-1.4\n",offsets=[0];
     for(let i=1;i<objs.length;i++){if(!objs[i])continue;offsets[i]=pdf.length;pdf+=`${i} 0 obj\n${objs[i]}\nendobj\n`}
     let xref=pdf.length,max=objs.length-1;pdf+=`xref\n0 ${max+1}\n0000000000 65535 f \n`;
     for(let i=1;i<=max;i++)pdf+=(String(offsets[i]||0).padStart(10,"0")+" 00000 n \n");
     pdf+=`trailer\n<< /Size ${max+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
     downloadBlob(new Blob([new Uint8Array([...pdf].map(c=>c.charCodeAt(0)&255))],{type:"application/pdf"}),`costi-carburante-${from}-${to}.pdf`);
    }
    function printReport(){window.print()}

    async function loadHistory(){
     let vid=$("vehicle").value,fromValue=$("dateFrom").value,toValue=$("dateTo").value;
     if(!vid){$("status").className="status err";$("status").textContent="Seleziona un veicolo.";return}
     if(!fromValue||!toValue){$("status").className="status err";$("status").textContent="Seleziona la data iniziale e la data finale.";return}
     if(fromValue>toValue){$("status").className="status err";$("status").textContent="La data iniziale non può essere successiva alla data finale.";return}
     let from=new Date(`${fromValue}T00:00:00`),to=new Date(`${toValue}T23:59:59.999`);
     exportEnabled(false);lastItems=[];$("load").disabled=true;$("status").className="status";$("status").textContent="Caricamento rifornimenti…";
     try{
      let fills=await get("FillUp",{deviceSearch:{id:vid},fromDate:from.toISOString(),toDate:to.toISOString()},50000);
      fills=(fills||[]).sort((a,b)=>new Date(b.dateTime||b.date)-new Date(a.dateTime||a.date));
      let v=vehicles.find(x=>x.id===vid)||{},vf=await fuelForVehicle(v),out=[];
      for(let i=0;i<fills.length;i++){
       $("status").textContent=`Analisi rifornimento ${i+1} di ${fills.length}: livello carburante → GPS → MIMIT…`;
       let fuelDiag;
       try{fuelDiag=await enrichFuelDiagnostic(fills[i],vid)}
       catch(e){fuelDiag={ok:false,rawCount:0,samples:[],events:[],message:"Errore diagnostica: "+e.message}}
       let item;
       try{item=await processFill(fills[i],vf,vid,fuelDiag)}
       catch(e){item={date:fills[i].dateTime||fills[i].date,originalDate:fills[i].dateTime||fills[i].date,liters:litersOf(fills[i]),fuel:vf,fuelDiag,error:e.message}}
       out.push(item);
      }
      lastItems=out;lastVehicle=v;render(out);exportEnabled(out.length>0);$("status").className="status ok";$("status").textContent=`Completato: ${fills.length} rifornimenti analizzati dal ${from.toLocaleDateString("it-IT")} al ${to.toLocaleDateString("it-IT")}.`;
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
    $("vehicle").addEventListener("change",()=>{exportEnabled(false);lastItems=[];updateVehiclePhoto()});
    $("pdf").addEventListener("click",exportPdf);
    $("excel").addEventListener("click",exportExcel);
    $("print").addEventListener("click",printReport);
    $("dateFrom").addEventListener("change",()=>{$("dateTo").min=$("dateFrom").value||""});
    window.geotab=window.geotab||{};window.geotab.addin=window.geotab.addin||{};
    window.geotab.addin.costiCarburante=function(){return {initialize:function(api,state,callback){init(api).finally(()=>callback&&callback())},focus:function(api,state){if(!window.__fuelApi){window.__fuelApi=true;api&&init(api)}},blur:function(){}}};
    })();
