import { useState, useEffect, useMemo, useCallback } from "react";

// ─── Supabase ─────────────────────────────────────────────────────────────────
async function sbFetch(url, key, path) {
  const r = await fetch(`${url}/rest/v1/${path}`, {
    headers: { apikey: key, Authorization: `Bearer ${key}` }
  });
  if (!r.ok) throw new Error(`Supabase: ${r.status}`);
  return r.json();
}

// ─── Demo data ────────────────────────────────────────────────────────────────
const DEMO = [
  { id:"1",  product:"геотекстиль 200 г/м²",    category:"геотекстиль", density:200, width:4.5, roll_length:150, thickness:null, price:18.50, unit:"м²", price_per_roll:12488, city:"Казань",          supplier_name:"Алия С.",   price_date:"2026-04-10", prev_price:17.80 },
  { id:"2",  product:"геотекстиль 200 г/м²",    category:"геотекстиль", density:200, width:4.5, roll_length:150, thickness:null, price:17.80, unit:"м²", price_per_roll:12006, city:"Самара",          supplier_name:"Рустам Д.", price_date:"2026-04-08", prev_price:18.20 },
  { id:"3",  product:"геотекстиль 200 г/м²",    category:"геотекстиль", density:200, width:4.5, roll_length:150, thickness:null, price:19.20, unit:"м²", price_per_roll:12960, city:"Нижний Новгород", supplier_name:"Марина В.", price_date:"2026-04-13", prev_price:19.20 },
  { id:"4",  product:"геотекстиль 300 г/м²",    category:"геотекстиль", density:300, width:3.0, roll_length:100, thickness:null, price:24.00, unit:"м²", price_per_roll:7200,  city:"Казань",          supplier_name:"Алия С.",   price_date:"2026-04-10", prev_price:22.50 },
  { id:"5",  product:"геотекстиль 400 г/м²",    category:"геотекстиль", density:400, width:4.5, roll_length:100, thickness:null, price:31.50, unit:"м²", price_per_roll:14175, city:"Самара",          supplier_name:"Рустам Д.", price_date:"2026-04-11", prev_price:32.00 },
  { id:"6",  product:"геотекстиль 600 г/м²",    category:"геотекстиль", density:600, width:4.5, roll_length:50,  thickness:null, price:46.00, unit:"м²", price_per_roll:10350, city:"Екатеринбург",    supplier_name:"Павел О.",  price_date:"2026-04-09", prev_price:null  },
  { id:"7",  product:"георешетка 20/20",         category:"георешетка",  density:null,width:6.0, roll_length:50,  thickness:null, price:95.00, unit:"м²", price_per_roll:28500, city:"Казань",          supplier_name:"Алия С.",   price_date:"2026-04-09", prev_price:91.00 },
  { id:"8",  product:"георешетка 40/40",         category:"георешетка",  density:null,width:6.0, roll_length:50,  thickness:null, price:140.00,unit:"м²", price_per_roll:42000, city:"Самара",          supplier_name:"Рустам Д.", price_date:"2026-04-07", prev_price:138.00},
  { id:"9",  product:"геомембрана ПВД 0.5 мм",  category:"геомембрана", density:null,width:6.0, roll_length:100, thickness:0.5,  price:65.00, unit:"м²", price_per_roll:39000, city:"Нижний Новгород", supplier_name:"Марина В.", price_date:"2026-04-12", prev_price:68.00 },
  { id:"10", product:"геомембрана ПВД 1.0 мм",  category:"геомембрана", density:null,width:6.0, roll_length:50,  thickness:1.0,  price:120.00,unit:"м²", price_per_roll:36000, city:"Казань",          supplier_name:"Алия С.",   price_date:"2026-04-10", prev_price:115.00},
  { id:"11", product:"геомембрана HDPE 1.5 мм", category:"геомембрана", density:null,width:6.8, roll_length:50,  thickness:1.5,  price:195.00,unit:"м²", price_per_roll:66300, city:"Самара",          supplier_name:"Рустам Д.", price_date:"2026-04-11", prev_price:null  },
  { id:"12", product:"дренажная мембрана 500г",  category:"дренаж",     density:500, width:2.0, roll_length:20,  thickness:null, price:88.00, unit:"м²", price_per_roll:3520,  city:"Самара",          supplier_name:"Рустам Д.", price_date:"2026-04-06", prev_price:85.00 },
  { id:"13", product:"спанбонд 60 г/м²",         category:"спанбонд",   density:60,  width:3.2, roll_length:200, thickness:null, price:9.80,  unit:"м²", price_per_roll:6272,  city:"Нижний Новгород", supplier_name:"Марина В.", price_date:"2026-04-13", prev_price:10.20 },
  { id:"14", product:"спанбонд 100 г/м²",        category:"спанбонд",   density:100, width:3.2, roll_length:150, thickness:null, price:15.20, unit:"м²", price_per_roll:7296,  city:"Казань",          supplier_name:"Алия С.",   price_date:"2026-04-11", prev_price:15.20 },
  { id:"15", product:"спанбонд 150 г/м²",        category:"спанбонд",   density:150, width:3.2, roll_length:100, thickness:null, price:20.50, unit:"м²", price_per_roll:6560,  city:"Екатеринбург",    supplier_name:"Павел О.",  price_date:"2026-04-08", prev_price:19.80 },
];

const DEMO_HIST = {
  "геотекстиль 200 г/м²": [
    {city:"Казань",supplier_name:"Алия С.",   price:18.50,unit:"м²",price_date:"2026-04-10",prev_price:17.80},
    {city:"Казань",supplier_name:"Алия С.",   price:17.80,unit:"м²",price_date:"2026-03-15",prev_price:19.00},
    {city:"Казань",supplier_name:"Алия С.",   price:19.00,unit:"м²",price_date:"2026-02-01",prev_price:null},
    {city:"Самара",supplier_name:"Рустам Д.", price:17.80,unit:"м²",price_date:"2026-04-08",prev_price:18.20},
    {city:"Самара",supplier_name:"Рустам Д.", price:18.20,unit:"м²",price_date:"2026-03-10",prev_price:null},
  ],
};

const CATS = ["Все","геотекстиль","георешетка","геомембрана","дренаж","спанбонд"];
const CC   = { геотекстиль:"#1a7a4a", георешетка:"#1a5ca8", геомембрана:"#7a1a6a", дренаж:"#a87a1a", спанбонд:"#1a7a7a" };

const fmt  = (n,d=2) => n!=null ? Number(n).toLocaleString("ru-RU",{minimumFractionDigits:d,maximumFractionDigits:d}) : "—";
const fmtI = (n)     => n!=null ? Number(n).toLocaleString("ru-RU",{maximumFractionDigits:0}) : "—";
const fmtD = (s)     => s ? new Date(s).toLocaleDateString("ru-RU",{day:"2-digit",month:"2-digit",year:"2-digit"}) : "—";

// ─── Sub-components ───────────────────────────────────────────────────────────
function Badge({ cat }) {
  return (
    <span style={{display:"inline-block",padding:"2px 8px",borderRadius:4,fontSize:11,fontWeight:600,
      background:(CC[cat]||"#888")+"20",color:CC[cat]||"#888"}}>
      {cat||"—"}
    </span>
  );
}

function Delta({ curr, prev }) {
  if (!prev) return <span style={{color:"#ddd",fontSize:12}}>—</span>;
  const d = curr - prev;
  if (Math.abs(d) < 0.01) return <span style={{color:"#ccc",fontSize:12}}>—</span>;
  return <span style={{fontSize:12,fontWeight:700,color:d>0?"#c0392b":"#27ae60"}}>{d>0?"▲":"▼"} {fmt(Math.abs(d))}</span>;
}

function AttrsRow({ row }) {
  const parts = [];
  if (row.density)     parts.push(`${fmtI(row.density)} г/м²`);
  if (row.width)       parts.push(`${row.width} м шир.`);
  if (row.roll_length) parts.push(`нам. ${fmtI(row.roll_length)} м`);
  if (row.thickness)   parts.push(`${row.thickness} мм`);
  if (row.color)       parts.push(row.color);
  if (row.material)    parts.push(row.material);
  return <span style={{color:"#777",fontSize:12,lineHeight:1.4}}>{parts.join(" · ") || "—"}</span>;
}

// ─── Settings modal ───────────────────────────────────────────────────────────
function SettingsModal({ init, onSave, onClose }) {
  const [url, setUrl] = useState(init.url);
  const [key, setKey] = useState(init.key);
  const inp = { width:"100%",padding:"9px 12px",border:"1.5px solid #e0ddd6",borderRadius:7,
    fontSize:13,fontFamily:"inherit",outline:"none",boxSizing:"border-box",marginTop:5,background:"#fff" };
  return (
    <div onClick={onClose} style={{position:"fixed",inset:0,background:"rgba(0,0,0,.5)",display:"flex",
      alignItems:"center",justifyContent:"center",zIndex:400}}>
      <div onClick={e=>e.stopPropagation()} style={{background:"#fff",borderRadius:14,padding:30,width:460,maxWidth:"94vw"}}>
        <div style={{fontWeight:700,fontSize:17,marginBottom:4}}>Подключение к Supabase</div>
        <div style={{color:"#999",fontSize:13,marginBottom:22}}>Без настройки — демо-данные</div>
        <label style={{fontSize:13,fontWeight:600,color:"#444",display:"block"}}>Project URL
          <input style={inp} value={url} onChange={e=>setUrl(e.target.value)} placeholder="https://xxxx.supabase.co"/>
        </label>
        <label style={{fontSize:13,fontWeight:600,color:"#444",display:"block",marginTop:14}}>Anon Key
          <input style={inp} type="password" value={key} onChange={e=>setKey(e.target.value)} placeholder="eyJhbGci..."/>
        </label>
        <div style={{display:"flex",gap:10,marginTop:22}}>
          <button onClick={()=>onSave(url,key)}
            style={{padding:"9px 22px",background:"#c8e84a",color:"#1c1c1a",border:"none",borderRadius:7,
              fontWeight:700,fontSize:14,cursor:"pointer",fontFamily:"inherit"}}>Сохранить</button>
          <button onClick={onClose}
            style={{padding:"9px 22px",background:"#f0ede6",color:"#666",border:"none",borderRadius:7,
              fontWeight:600,fontSize:14,cursor:"pointer",fontFamily:"inherit"}}>Отмена</button>
        </div>
      </div>
    </div>
  );
}

// ─── History panel ────────────────────────────────────────────────────────────
function HistoryPanel({ row, items, onClose }) {
  const grouped = items.reduce((a,it)=>{
    const k=`${it.city} · ${it.supplier_name}`;
    if(!a[k]) a[k]=[];
    a[k].push(it);
    return a;
  },{});

  return (
    <div style={{position:"fixed",right:0,top:56,bottom:0,width:320,background:"#fff",
      borderLeft:"1px solid #e8e5de",zIndex:200,overflowY:"auto",padding:22,
      boxShadow:"-8px 0 28px rgba(0,0,0,.08)"}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:18}}>
        <div>
          <div style={{fontWeight:700,fontSize:13,lineHeight:1.4,maxWidth:240}}>{row.product}</div>
          <div style={{color:"#aaa",fontSize:11,marginTop:3}}>история цен</div>
        </div>
        <button onClick={onClose} style={{background:"none",border:"none",fontSize:22,cursor:"pointer",color:"#bbb",lineHeight:1,padding:0}}>×</button>
      </div>

      {/* Attrs card */}
      <div style={{background:"#f6f5f1",borderRadius:8,padding:"10px 14px",marginBottom:18,display:"flex",gap:16,flexWrap:"wrap"}}>
        {[
          row.density     && { label:"Плотность",   val:`${fmtI(row.density)} г/м²` },
          row.width       && { label:"Ширина",       val:`${row.width} м` },
          row.roll_length && { label:"Намотка",      val:`${fmtI(row.roll_length)} м` },
          row.thickness   && { label:"Толщина",      val:`${row.thickness} мм` },
          row.material    && { label:"Материал",     val:row.material },
        ].filter(Boolean).map(({label,val})=>(
          <div key={label}>
            <div style={{fontSize:10,color:"#bbb",textTransform:"uppercase",letterSpacing:.8}}>{label}</div>
            <div style={{fontWeight:700,fontSize:13,marginTop:1}}>{val}</div>
          </div>
        ))}
      </div>

      {items.length===0 && (
        <div style={{color:"#ccc",fontSize:12,textAlign:"center",padding:"20px 0"}}>
          Нет истории в базе
        </div>
      )}

      {Object.entries(grouped).map(([key, rows]) => (
        <div key={key} style={{marginBottom:16}}>
          <div style={{fontSize:10,fontWeight:700,color:"#999",letterSpacing:.5,
            marginBottom:7,paddingBottom:5,borderBottom:"1px solid #f0ede6",textTransform:"uppercase"}}>{key}</div>
          {rows.map((r,i) => {
            const d = r.prev_price ? r.price - r.prev_price : null;
            return (
              <div key={i} style={{display:"flex",justifyContent:"space-between",alignItems:"center",
                padding:"4px 0",borderBottom:"1px solid #f9f8f5"}}>
                <span style={{color:"#bbb",fontSize:11,minWidth:52}}>{fmtD(r.price_date)}</span>
                <span style={{fontFamily:"monospace",fontSize:13,fontWeight:700}}>{fmt(r.price)} р</span>
                {d!=null
                  ? <span style={{fontSize:11,fontWeight:700,minWidth:46,textAlign:"right",
                      color:d>0?"#c0392b":d<0?"#27ae60":"#ccc"}}>
                      {d>0?"▲":d<0?"▼":"—"}{d!==0?" "+fmt(Math.abs(d)):""}
                    </span>
                  : <span style={{minWidth:46}}/>
                }
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// ─── Main App ─────────────────────────────────────────────────────────────────
export default function App() {
  const [sbUrl, setSbUrl] = useState(() => (typeof localStorage!=="undefined" && localStorage.getItem("sb_url")) || "");
  const [sbKey, setSbKey] = useState(() => (typeof localStorage!=="undefined" && localStorage.getItem("sb_key")) || "");
  const [data,  setData]  = useState(DEMO);
  const [hist,  setHist]  = useState([]);
  const [loading, setLoading]   = useState(false);
  const [error,   setError]     = useState(null);
  const [showCfg, setShowCfg]   = useState(false);

  const [search, setSearch] = useState("");
  const [cat,    setCat]    = useState("Все");
  const [city,   setCity]   = useState("Все");
  const [sort,   setSort]   = useState("price");
  const [hov,    setHov]    = useState(null);
  const [sel,    setSel]    = useState(null);

  const demo = !sbUrl || !sbKey;

  const load = useCallback(async () => {
    if (demo) { setData(DEMO); return; }
    setLoading(true); setError(null);
    try {
      const rows = await sbFetch(sbUrl, sbKey, "prices_latest?select=*&order=price_date.desc&limit=2000");
      setData(rows.length ? rows : DEMO);
    } catch(e) { setError(e.message); setData(DEMO); }
    setLoading(false);
  }, [sbUrl, sbKey, demo]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!sel) return;
    if (demo) { setHist(DEMO_HIST[sel.product] || []); return; }
    const q = encodeURIComponent(`%${sel.product}%`);
    sbFetch(sbUrl, sbKey, `prices_history?product=ilike.${q}&order=price_date.desc&limit=40&select=*`)
      .then(setHist).catch(() => setHist([]));
  }, [sel, sbUrl, sbKey, demo]);

  const cities = useMemo(() => ["Все", ...new Set(data.map(r=>r.city).filter(Boolean))], [data]);

  const rows = useMemo(() => {
    let r = data;
    if (cat !== "Все")  r = r.filter(x => x.category === cat);
    if (city !== "Все") r = r.filter(x => x.city === city);
    if (search.trim()) {
      const q = search.toLowerCase();
      r = r.filter(x =>
        x.product.includes(q) ||
        (x.category||"").includes(q) ||
        (x.city||"").includes(q) ||
        (x.supplier_name||"").includes(q)
      );
    }
    return [...r].sort((a,b) =>
      sort==="price" ? a.price-b.price :
      sort==="price_desc" ? b.price-a.price :
      sort==="date" ? new Date(b.price_date)-new Date(a.price_date) :
      a.product.localeCompare(b.product,"ru")
    );
  }, [data, cat, city, search, sort]);

  const saveCfg = (url, key) => {
    setSbUrl(url); setSbKey(key);
    if (typeof localStorage !== "undefined") {
      localStorage.setItem("sb_url", url);
      localStorage.setItem("sb_key", key);
    }
    setShowCfg(false);
  };

  const TH_S = {background:"#1c1c1a",color:"#c8e84a",padding:"9px 11px",textAlign:"left",
    fontWeight:600,fontSize:10,letterSpacing:.8,textTransform:"uppercase",whiteSpace:"nowrap"};
  const TD_S = {padding:"9px 11px",verticalAlign:"middle"};

  return (
    <div style={{fontFamily:"'DM Sans','Segoe UI',sans-serif",background:"#f4f2ee",minHeight:"100vh",color:"#1c1c1a"}}>
      <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@400;500;600&family=JetBrains+Mono:wght@700&display=swap" rel="stylesheet"/>

      {/* ── Topbar ── */}
      <div style={{background:"#1c1c1a",padding:"0 20px",height:56,display:"flex",alignItems:"center",
        justifyContent:"space-between",position:"sticky",top:0,zIndex:100}}>
        <span style={{fontFamily:"'Bebas Neue',sans-serif",fontSize:22,letterSpacing:2,color:"#c8e84a"}}>
          СНАБЖЕНЕЦ · ПРАЙСЫ
        </span>
        <div style={{display:"flex",gap:10,alignItems:"center"}}>
          {loading && <span style={{color:"#666",fontSize:12}}>загрузка...</span>}
          {error   && <span style={{color:"#e74c3c",fontSize:12}}>⚠ {error.slice(0,30)}</span>}
          {demo    && <span style={{fontSize:11,color:"#555",background:"#252523",padding:"3px 9px",borderRadius:5}}>ДЕМО</span>}
          <button onClick={load} style={{background:"none",border:"1px solid #333",borderRadius:6,
            color:"#888",fontSize:12,padding:"5px 10px",cursor:"pointer",fontFamily:"inherit"}}>↻</button>
          <button onClick={()=>setShowCfg(true)} style={{background:"none",border:"1px solid #444",
            borderRadius:6,color:"#c8e84a",fontSize:12,padding:"5px 12px",cursor:"pointer",fontFamily:"inherit"}}>
            ⚙ Supabase
          </button>
        </div>
      </div>

      <div style={{display:"flex",minHeight:"calc(100vh - 56px)"}}>
        {/* ── Sidebar ── */}
        <div style={{width:188,flexShrink:0,background:"#fff",borderRight:"1px solid #e8e5de",
          padding:"18px 0",position:"sticky",top:56,height:"calc(100vh - 56px)",overflowY:"auto"}}>
          <div style={{fontSize:10,fontWeight:700,color:"#bbb",letterSpacing:1.5,
            padding:"0 16px 8px",textTransform:"uppercase"}}>Категория</div>
          {CATS.map(c => (
            <div key={c} onClick={()=>setCat(c)} style={{
              display:"flex",alignItems:"center",gap:8,padding:"8px 16px",cursor:"pointer",
              fontSize:13,background:cat===c?"#f4f2ee":"transparent",
              fontWeight:cat===c?600:400,color:cat===c?"#1c1c1a":"#666",
              borderRight:cat===c?"3px solid #c8e84a":"3px solid transparent",
              transition:"all .12s",
            }}>
              {c!=="Все" && <span style={{width:8,height:8,borderRadius:"50%",
                background:CC[c]||"#999",flexShrink:0}}/>}
              <span>{c==="Все" ? "Все категории" : c}</span>
            </div>
          ))}

          <div style={{margin:"22px 16px 0",paddingTop:18,borderTop:"1px solid #f0ede6"}}>
            <div style={{fontSize:10,fontWeight:700,color:"#bbb",letterSpacing:1.5,
              textTransform:"uppercase",marginBottom:4}}>Найдено</div>
            <div style={{fontSize:32,fontFamily:"'Bebas Neue',sans-serif",letterSpacing:1,lineHeight:1}}>
              {rows.length}
            </div>
            <div style={{fontSize:11,color:"#bbb",marginTop:2}}>позиций</div>
          </div>

          <div style={{margin:"18px 16px 0",paddingTop:16,borderTop:"1px solid #f0ede6"}}>
            <div style={{fontSize:10,fontWeight:700,color:"#bbb",letterSpacing:1.5,
              textTransform:"uppercase",marginBottom:8}}>Источник</div>
            <div style={{fontSize:12,color:demo?"#e67e22":"#27ae60",fontWeight:600}}>
              {demo ? "● Демо-данные" : "● Supabase"}
            </div>
            {!demo && <div style={{fontSize:11,color:"#bbb",marginTop:3,wordBreak:"break-all"}}>
              {sbUrl.replace("https://","").slice(0,22)}...
            </div>}
          </div>
        </div>

        {/* ── Main ── */}
        <div style={{flex:1,padding:18,minWidth:0,
          paddingRight:sel?338:18,transition:"padding-right .2s"}}>
          {/* Filters */}
          <div style={{display:"flex",gap:8,marginBottom:16,flexWrap:"wrap",alignItems:"center"}}>
            <input value={search} onChange={e=>setSearch(e.target.value)}
              placeholder="Поиск: геотекстиль, мембрана, спанбонд 60..."
              style={{flex:1,minWidth:180,padding:"9px 13px",border:"1.5px solid #e0ddd6",
                borderRadius:7,fontSize:13,background:"#fff",outline:"none",fontFamily:"inherit"}}/>
            <select value={city} onChange={e=>setCity(e.target.value)}
              style={{padding:"9px 11px",border:"1.5px solid #e0ddd6",borderRadius:7,fontSize:12,
                background:"#fff",cursor:"pointer",fontFamily:"inherit",color:"#444",outline:"none"}}>
              {cities.map(c=><option key={c}>{c}</option>)}
            </select>
            <select value={sort} onChange={e=>setSort(e.target.value)}
              style={{padding:"9px 11px",border:"1.5px solid #e0ddd6",borderRadius:7,fontSize:12,
                background:"#fff",cursor:"pointer",fontFamily:"inherit",color:"#444",outline:"none"}}>
              <option value="price">Цена ↑</option>
              <option value="price_desc">Цена ↓</option>
              <option value="date">По дате</option>
              <option value="product">А–Я</option>
            </select>
          </div>

          {/* Table */}
          <div style={{overflowX:"auto",borderRadius:10,border:"1px solid #e8e5de",
            boxShadow:"0 1px 4px rgba(0,0,0,.04)"}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:12,background:"#fff"}}>
              <thead>
                <tr>
                  {["Товар","Кат.","Плотн.","Ширина","Намотка","Толщ.","Матер.",
                    "Цена/м²","Рулон","Город","Снабженец","Дата","Δ"].map(h=>(
                    <th key={h} style={TH_S}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.length===0 && (
                  <tr><td colSpan={13} style={{textAlign:"center",padding:"48px 20px",color:"#ccc",fontSize:14}}>
                    Ничего не найдено
                  </td></tr>
                )}
                {rows.map(row => (
                  <tr key={row.id}
                    style={{background:hov===row.id||sel?.id===row.id?"#f9f8f5":"#fff",
                      borderBottom:"1px solid #f0ede6",cursor:"pointer",transition:"background .1s"}}
                    onMouseEnter={()=>setHov(row.id)}
                    onMouseLeave={()=>setHov(null)}
                    onClick={()=>setSel(sel?.id===row.id?null:row)}>
                    <td style={{...TD_S,maxWidth:220}}>
                      <div style={{fontWeight:sel?.id===row.id?600:400,fontSize:12,lineHeight:1.35}}>
                        {row.product}
                      </div>
                      {row.product_original && row.product_original!==row.product && (
                        <div style={{fontSize:10,color:"#ccc",marginTop:1}}>{row.product_original}</div>
                      )}
                    </td>
                    <td style={TD_S}><Badge cat={row.category}/></td>
                    <td style={{...TD_S,color:"#666",whiteSpace:"nowrap"}}>{row.density?`${fmtI(row.density)} г/м²`:"—"}</td>
                    <td style={{...TD_S,color:"#666",whiteSpace:"nowrap"}}>{row.width?`${row.width} м`:"—"}</td>
                    <td style={{...TD_S,color:"#666",whiteSpace:"nowrap"}}>{row.roll_length?`${fmtI(row.roll_length)} м`:"—"}</td>
                    <td style={{...TD_S,color:"#666",whiteSpace:"nowrap"}}>{row.thickness?`${row.thickness} мм`:"—"}</td>
                    <td style={{...TD_S,color:"#999",whiteSpace:"nowrap"}}>{row.material||"—"}</td>
                    <td style={TD_S}>
                      <span style={{fontFamily:"'JetBrains Mono',monospace",fontWeight:700,fontSize:13,
                        color:"#1c1c1a"}}>{fmt(row.price)} р</span>
                      <div style={{fontSize:10,color:"#bbb"}}>{row.unit}</div>
                    </td>
                    <td style={{...TD_S,fontFamily:"monospace",fontSize:11,color:"#999",whiteSpace:"nowrap"}}>
                      {row.price_per_roll?`${fmtI(row.price_per_roll)} р`:"—"}
                    </td>
                    <td style={{...TD_S,whiteSpace:"nowrap",color:"#444",fontSize:12}}>{row.city}</td>
                    <td style={{...TD_S,color:"#888",fontSize:11,whiteSpace:"nowrap"}}>{row.supplier_name}</td>
                    <td style={{...TD_S,color:"#bbb",fontSize:11,whiteSpace:"nowrap"}}>{fmtD(row.price_date)}</td>
                    <td style={TD_S}><Delta curr={row.price} prev={row.prev_price}/></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{marginTop:10,color:"#ccc",fontSize:11}}>
            {demo
              ? "Демо-данные · Нажмите ⚙ Supabase и введите ключи для подключения к базе"
              : `${rows.length} позиций · обновлено ${new Date().toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"})}`
            }
            {!demo && <span style={{marginLeft:12,cursor:"pointer",color:"#bbb",textDecoration:"underline"}}
              onClick={load}>обновить</span>}
          </div>
        </div>
      </div>

      {sel && <HistoryPanel row={sel} items={hist} onClose={()=>setSel(null)}/>}
      {showCfg && <SettingsModal init={{url:sbUrl,key:sbKey}} onSave={saveCfg} onClose={()=>setShowCfg(false)}/>}
    </div>
  );
}
