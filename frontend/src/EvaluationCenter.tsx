import {ChevronDown, ChevronRight, ChevronLeft, LayoutGrid, UserSquare2, Sparkles, FlaskConical, RefreshCw, X} from 'lucide-react';
import {GoldenReview, DatasetReplay, evaluationPost} from './EvaluationReview';
import { useEffect, useState } from 'react';
import {buildEvaluationCaseQuery} from './evaluationViewModel';
import './evaluation.css';

export const moduleLabels: Record<string, string> = {request_understanding: '人才要求理解', job_matching: '岗位名称匹配', dimension_generation: '动态维度生成', fact_extraction: '事实抽取'};
type CaseRow = {annotation?: {score: number | null; review_status: string}; case_id: string; source_run_id: string; turn_no: number; module_key: string; invocation_no: number; status: string; created_at: string; identity: Record<string, string>; has_feedback: boolean; run_has_feedback: boolean};
async function api(url: string, options?: RequestInit) {
  const r = await fetch(url, options);
  const body = await r.json();
  if (!r.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '请求失败');
  return body;
}
export function FeedbackButton({apiBase, runId, caseId}: {apiBase: string; runId: string; caseId?: string}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const [state, setState] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit() {
    setBusy(true);
    try {
      await api(`${apiBase}/evaluation/feedback`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({source_run_id: runId, case_id: caseId || null, feedback_scope: caseId ? 'module' : 'run', note})});
      setState('已提交用户反馈'); setOpen(false);
    } catch (e) { setState(String(e)); } finally { setBusy(false); }
  }
  return <div className="feedbackForm"><button onClick={() => setOpen(!open)}>问题反馈</button>{state && <small role="status">{state}</small>}{open && <div><textarea aria-label="反馈说明" placeholder="简短说明（可选）" value={note} maxLength={2000} onChange={e => setNote(e.target.value)} /><button disabled={busy} onClick={submit}>{busy ? '提交中' : '提交反馈'}</button></div>}</div>;
}
export function StepCaseFeedback({apiBase, runId, stepId}: {apiBase: string; runId: string; stepId: string}) {
  const [rows, setRows] = useState<CaseRow[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let current = true;
    setRows([]); setError('');
    async function loadCases() {
      const modules = stepId === 'understand_request' ? ['request_understanding', 'job_matching'] : stepId === 'build_dimensions' ? ['dimension_generation'] : stepId === 'parallel_assessment' ? ['fact_extraction'] : [];
      if (!modules.length) return;
      const collected: CaseRow[] = [];
      let offset = 0;
      while (current) {
        const data = await api(`${apiBase}/evaluation/cases?source_run_id=${encodeURIComponent(runId)}&limit=200&offset=${offset}`);
        collected.push(...data.items);
        if (!data.has_more) break;
        offset += 200;
      }
      if (current) setRows(collected.filter(row => modules.includes(row.module_key)));
    }
    loadCases().catch(e => { if (current) setError(String(e)); });
    return () => {current = false;};
  }, [apiBase, runId, stepId]);
  return <section><h3>模块调用反馈</h3>{error && <p role="alert">{error}</p>}{rows.map(row => <div key={row.case_id}><b>{moduleLabels[row.module_key]} · 调用 {row.invocation_no}</b><small>{Object.values(row.identity).join(' / ')} · {row.case_id.slice(0, 8)}</small><FeedbackButton apiBase={apiBase} runId={runId} caseId={row.case_id} /></div>)}{!rows.length && !error && <p>该步骤暂无可反馈的模块调用</p>}</section>;
}

const PAGE_SIZE = 10;
const timeLabel = (value?: string | null) => value ? new Date(value).toLocaleString() : '尚未完成';
type RunGroupRow = {source_run_id:string; conversation_title:string|null; completed_at:string|null; case_count:number};

export function EvaluationPagination({offset, more, busy, onChange, unit='条'}: {offset:number; more:boolean; busy?:boolean; onChange:(n:number)=>void; unit?:string}) {
  return <div className="evaluationPagination"><span>每页 10 {unit} · 第 {offset / PAGE_SIZE + 1} 页</span><div><button disabled={busy || offset===0} onClick={()=>onChange(offset-PAGE_SIZE)}>上一页</button><button disabled={busy || !more} onClick={()=>onChange(offset+PAGE_SIZE)}>下一页</button></div></div>;
}
function CaseTable({rows, selected, busy, onSelect, onOpen}: {rows:CaseRow[]; selected:string[]; busy:boolean; onSelect:(id:string, checked:boolean)=>void; onOpen:(id:string)=>void}) {
  return <div className="evaluationTableWrap"><table><thead><tr><th>选择</th><th>模块 / 调用</th><th>得分 / 审核</th><th>业务对象</th><th>状态</th><th>反馈</th><th>采集时间</th></tr></thead><tbody>{rows.map(row=><tr key={row.case_id} tabIndex={0} className="evaluationClickableRow" aria-label={`${moduleLabels[row.module_key]} 调用 ${row.invocation_no} 详情`} onClick={()=>onOpen(row.case_id)} onKeyDown={e=>{if(e.target===e.currentTarget && (e.key==='Enter'||e.key===' ')){e.preventDefault();onOpen(row.case_id);}}}>
    <td onClick={e=>e.stopPropagation()}><input aria-label={`选择 ${row.case_id}`} type="checkbox" checked={selected.includes(row.case_id)} disabled={busy || !!row.annotation || (!selected.includes(row.case_id)&&selected.length>=20)} onChange={e=>onSelect(row.case_id,e.target.checked)}/></td>
    <td><b>{moduleLabels[row.module_key]}</b><small>调用 #{row.invocation_no} · Turn {row.turn_no}</small></td><td>{row.annotation ? <><b>{row.annotation.score ?? '未评分'}</b><small>{row.annotation.review_status==='confirmed'?'已确认':'待审核'}</small></> : '未标注'}</td><td>{Object.values(row.identity).join(' / ')||'—'}</td><td><span className={`evaluationBadge ${row.status}`}>{row.status==='succeeded'?'成功':'失败'}</span></td><td>{row.has_feedback?'用户反馈':row.run_has_feedback?'Run 有反馈':'—'}</td><td>{timeLabel(row.created_at)}</td>
  </tr>)}</tbody></table></div>;
}
function RunGroup({group, apiBase, query, refresh, selected, busy, onSelect, onOpen}: {group:RunGroupRow; apiBase:string; query:string; refresh:number; selected:string[]; busy:boolean; onSelect:(id:string, checked:boolean)=>void; onOpen:(id:string)=>void}) {
  const [open,setOpen]=useState(false);
  const [offset,setOffset]=useState(0);
  const [data,setData]=useState<{items:CaseRow[];has_more:boolean}>({items:[],has_more:false});
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(false);
  useEffect(()=>{
    if(!open)return;
    let active=true;setLoading(true);setError('');
    const q=new URLSearchParams(query);q.set('source_run_id',group.source_run_id);q.set('offset',String(offset));q.set('limit','10');
    api(`${apiBase}/evaluation/cases?${q}`).then(d=>{if(active)setData(d);}).catch(e=>{if(active)setError(String(e));}).finally(()=>{if(active)setLoading(false);});
    return ()=>{active=false;};
  },[open,offset,query,refresh,apiBase,group.source_run_id]);
  return <section className="evaluationRunGroup"><button className="evaluationRunHeading" aria-expanded={open} onClick={()=>setOpen(!open)}>
    {open?<ChevronDown size={18}/>:<ChevronRight size={18}/>}<span className="evaluationRunTitle"><b>{group.conversation_title||'未命名会话'}</b><small>Run ID · {group.source_run_id}</small></span><span className="evaluationRunTime"><small>完成时间</small>{timeLabel(group.completed_at)}</span><span className="evaluationCount">{group.case_count} 条 Case</span>
  </button>{open&&<div>{loading?<p className="evaluationEmpty">加载样例中…</p>:error?<p role="alert" className="evaluationError">{error}</p>:<><CaseTable rows={data.items} selected={selected} busy={busy} onSelect={onSelect} onOpen={onOpen}/>{!data.items.length&&<p className="evaluationEmpty">暂无符合条件的样例</p>}<EvaluationPagination offset={offset} more={data.has_more} busy={busy} onChange={setOffset}/></>}</div>}</section>;
}
export function EvaluationCenter({apiBase, onDashboard, onChat, onRecords, sidebarCollapsed, onToggleSidebar}: {apiBase:string; onDashboard:()=>void; onChat:()=>void; onRecords:()=>void; sidebarCollapsed:boolean; onToggleSidebar:()=>void}) {
  const [tab,setTab]=useState('cases');
  const [selected,setSelected]=useState<string[]>([]);
  const [busy,setBusy]=useState(false);
  const [batchResult,setBatchResult]=useState<any>(null);
  const [quality,setQuality]=useState('');
  const [after,setAfter]=useState('');
  const [before,setBefore]=useState('');
  const [groupByRun,setGroupByRun]=useState(false);
  const [module,setModule]=useState('');
  const [status,setStatus]=useState('');
  const [signal,setSignal]=useState(false);
  const [run,setRun]=useState('');
  const [offset,setOffset]=useState(0);
  const [data,setData]=useState<{items:any[];has_more:boolean}>({items:[],has_more:false});
  const [detail,setDetail]=useState<any>(null);
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(false);
  const [refresh,setRefresh]=useState(0);
  const q=new URLSearchParams();
  if(quality==='unannotated')q.set('unannotated','true');
  if(quality==='low')q.set('max_score','2');
  if(after && !Number.isNaN(Date.parse(after)))q.set('created_after',new Date(after).toISOString());
  if(before && !Number.isNaN(Date.parse(before)))q.set('created_before',new Date(before).toISOString());
  if(module)q.set('module_key',module);
  if(status)q.set('status',status);
  if(signal)q.set('has_feedback','true');
  if(run)q.set('source_run_id',run);
  const query=q.toString();
  useEffect(()=>{
    let active=true;setError('');setSelected([]);setLoading(true);setData({items:[],has_more:false});
    const params=buildEvaluationCaseQuery(query,groupByRun,offset);
    api(`${apiBase}/evaluation/cases?${params}`).then(d=>{if(active)setData(d);}).catch(e=>{if(active)setError(String(e));}).finally(()=>{if(active)setLoading(false);});
    return ()=>{active=false;};
  },[apiBase,query,groupByRun,offset,refresh]);
  function select(id:string,checked:boolean){setSelected(current=>checked?[...new Set([...current,id])]:current.filter(x=>x!==id));}
  async function openCase(id:string){try{setDetail(await api(`${apiBase}/evaluation/cases/${id}`));}catch(e){setError(String(e));}}
  async function judgeSelected(){setBusy(true);setError('');try{setBatchResult(await evaluationPost(`${apiBase}/evaluation/judge`,{case_ids:selected}));setRefresh(x=>x+1);}catch(e){setError(String(e));}finally{setBusy(false);}}
  function changeRunGrouping(checked:boolean){
    setData({items:[],has_more:false});
    setError('');
    setOffset(0);
    setGroupByRun(checked);
  }
  return <div className={`app evaluationApp${sidebarCollapsed?' sidebarCollapsed':''}`}>
    <aside className="side"><div className="brand"><img src="/talentos-favicon.png" alt="TalentOS"/><div><b>智鉴 TalentOS</b><small>多 AGENT 人才决策平台</small></div></div><nav>
      <button onClick={onDashboard}><LayoutGrid/><span>工作台</span></button><button onClick={onRecords}><UserSquare2/><span>人才档案</span></button><button onClick={onChat}><Sparkles/><span>评估与推荐</span></button><button className="active"><FlaskConical/><span>评测中心</span></button>
    </nav><div className="env"><div><i/><b>测试环境</b></div><small>服务正常 · V1.0</small></div><button type="button" className="sideToggle" onClick={onToggleSidebar} aria-label={sidebarCollapsed?'展开导航栏':'收起导航栏'} aria-expanded={!sidebarCollapsed}>{sidebarCollapsed?<ChevronRight/>:<ChevronLeft/>}</button></aside>
    <main className="evaluationMain"><header><div><span>评测中心</span><ChevronRight/><b>{tab==='cases'?'评测样例':'数据集'}</b></div><div className="currentUser"><b>Will</b><span>W</span></div></header>
      <div className="evaluationContent"><div className="moduleTabs" role="tablist" aria-label="评测中心功能"><button role="tab" aria-selected={tab==='cases'} className={tab==='cases'?'active':''} onClick={()=>setTab('cases')}>评测样例</button><button role="tab" aria-selected={tab==='dataset'} className={tab==='dataset'?'active':''} onClick={()=>setTab('dataset')}>数据集</button></div>
      <div hidden={tab!=='cases'} role="tabpanel" aria-label="评测样例">
        <div className="evaluationToolbar">
          <fieldset disabled={busy} className="evaluationFilterFields"><label className="evaluationCheckLabel evaluationGroupCheck"><input type="checkbox" checked={groupByRun} onChange={e=>changeRunGrouping(e.target.checked)}/>按 Run 分组查看</label><label>模块<select value={module} onChange={e=>{setModule(e.target.value);setOffset(0);}}><option value="">全部模块</option>{Object.entries(moduleLabels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label><label>调用状态<select value={status} onChange={e=>{setStatus(e.target.value);setOffset(0);}}><option value="">全部状态</option><option value="succeeded">调用成功</option><option value="failed">调用失败</option></select></label><label className="evaluationCheckLabel"><input type="checkbox" checked={quality==='low'} onChange={e=>{setQuality(e.target.checked?'low':'');setOffset(0);}}/>仅低分 Case（0—2 分）</label><label className="evaluationCheckLabel"><input type="checkbox" checked={quality==='unannotated'} onChange={e=>{setQuality(e.target.checked?'unannotated':'');setOffset(0);}}/>仅未标注</label><label>Run ID<input placeholder="输入完整 Run ID" value={run} onChange={e=>{setRun(e.target.value);setOffset(0);}}/></label><label>采集开始时间<input type="datetime-local" value={after} onChange={e=>{setAfter(e.target.value);setOffset(0);}}/></label><label>采集结束时间<input type="datetime-local" value={before} onChange={e=>{setBefore(e.target.value);setOffset(0);}}/></label><label className="evaluationCheckLabel"><input type="checkbox" checked={signal} onChange={e=>{setSignal(e.target.checked);setOffset(0);}}/>仅用户反馈 Case</label></fieldset>
          <div className="evaluationActions"><span>已选 {selected.length} 条 · 每批最多 20 条</span><div><button disabled={busy||loading} onClick={()=>setRefresh(x=>x+1)}><RefreshCw size={15}/>刷新</button><button className="evaluationPrimary" disabled={busy||!selected.length} onClick={judgeSelected}>{busy?'Judge 执行中…':'批量 Judge'}</button></div></div>
        </div>
        {batchResult&&<details className="evaluationResult" open><summary>批量 Judge 结果</summary><pre>{JSON.stringify(batchResult,null,2)}</pre></details>}{error&&<p role="alert" className="evaluationError">{error}</p>}
        {loading?<p className="evaluationEmpty">加载样例中…</p>:<>{!data.items.length&&<p className="evaluationEmpty">暂无符合条件的样例</p>}{groupByRun?data.items.map(group=><RunGroup key={`${query}:${offset}:${group.source_run_id}`} group={group} apiBase={apiBase} query={query} refresh={refresh} selected={selected} busy={busy} onSelect={select} onOpen={openCase}/>):<CaseTable rows={data.items} selected={selected} busy={busy} onSelect={select} onOpen={openCase}/>}<EvaluationPagination offset={offset} more={data.has_more} busy={busy} onChange={setOffset} unit={groupByRun?'个 Run':'条'}/></>}
      </div><div hidden={tab!=='dataset'} role="tabpanel" aria-label="数据集"><DatasetReplay apiBase={apiBase} refresh={refresh} moduleLabels={moduleLabels}/></div></div>
    </main>{detail&&<div className="evaluationOverlay" onClick={()=>setDetail(null)}><aside role="dialog" aria-label="Case 详情" onClick={e=>e.stopPropagation()}><button className="evaluationClose" aria-label="关闭详情" onClick={()=>setDetail(null)}><X/></button><h2>{moduleLabels[detail.module_key]} · Case 详情</h2><p>{detail.id}</p><p>Run · {detail.source_run_id} / Turn {detail.turn_no} / 调用 {detail.invocation_no}</p><GoldenReview key={detail.id} apiBase={apiBase} detail={detail} onSaved={()=>{setRefresh(x=>x+1);openCase(detail.id);}}/>{['input_payload','output_payload','replay_context','version_snapshot','status','error_code','duration_ms','feedback'].map(key=><section key={key}><h3>{({input_payload:'实际输入',output_payload:'实际输出',replay_context:'重放上下文',version_snapshot:'版本快照',status:'调用状态',error_code:'异常类型',duration_ms:'耗时 ms',feedback:'用户反馈'} as Record<string,string>)[key]}</h3><pre>{JSON.stringify(detail[key],null,2)}</pre></section>)}</aside></div>}
  </div>;
}
