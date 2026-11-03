import {useEffect, useState} from 'react';

export async function evaluationPost(url: string, payload: unknown) {
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const body = await r.json();
  if (!r.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '请求失败');
  return body;
}
export function GoldenReview({apiBase, detail, onSaved}: {apiBase:string; detail:any; onSaved:()=>void}) {
  const [golden, setGolden] = useState(JSON.stringify(detail.dataset_item?.golden_output ?? detail.annotation?.golden_candidate ?? detail.output_payload, null, 2));
  const [split, setSplit] = useState('test');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const confirmed = detail.annotation?.review_status === 'confirmed';
  async function confirm() {
    setBusy(true); setNote('');
    try {
      await evaluationPost(`${apiBase}/evaluation/cases/${detail.id}/confirm`, {golden_output:JSON.parse(golden), dataset_split:split, tags:[]});
      setNote('已确认并保存固定数据集条目'); onSaved();
    } catch(e) {setNote(String(e));} finally {setBusy(false);}
  }
  return <section><h3>质量标注与 Golden 审核</h3>{detail.annotation ? <pre>{JSON.stringify(detail.annotation, null, 2)}</pre> : <p>尚未标注，可人工编写参考答案</p>}
    <p>对照原始输入核验条件、字段与事实；无候选答案时编辑区使用实际输出，需人工修正后确认</p>
    <textarea aria-label="Golden JSON" className="goldenEditor" disabled={confirmed || busy} value={golden} onChange={e=>setGolden(e.target.value)}/>
    <select aria-label="数据集划分" disabled={confirmed || busy} value={split} onChange={e=>setSplit(e.target.value)}><option value="test">测试集</option><option value="dev">开发集</option></select>
    <button disabled={busy || confirmed} onClick={confirm}>{confirmed ? '已确认入集' : busy ? '保存中' : '确认 Golden 并入集'}</button><p role="status">{note}</p>
  </section>;
}
export function DatasetReplay({apiBase, refresh, moduleLabels}: {apiBase:string; refresh:number; moduleLabels:Record<string,string>}) {
  const [items,setItems]=useState<any[]>([]);
  const [selected,setSelected]=useState<string[]>([]);
  const [result,setResult]=useState<any>(null);
  const [detail,setDetail]=useState<any>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [loading,setLoading]=useState(false);
  const [offset,setOffset]=useState(0);
  const [more,setMore]=useState(false);
  const [split,setSplit]=useState('');
  const [module,setModule]=useState('');
  const [reload,setReload]=useState(0);
  useEffect(()=>{
    let active=true;setSelected([]);setLoading(true);setError('');setItems([]);
    const q=new URLSearchParams({offset:String(offset),limit:'10'});
    if(split)q.set('dataset_split',split);
    if(module)q.set('module_key',module);
    fetch(`${apiBase}/evaluation/dataset?${q}`).then(async r=>{if(!r.ok)throw new Error('数据集加载失败');return r.json();}).then(d=>{if(active){setItems(d.items);setMore(d.has_more);}}).catch(e=>{if(active)setError(String(e));}).finally(()=>{if(active)setLoading(false);});
    return ()=>{active=false;};
  },[apiBase,refresh,offset,split,module,reload]);
  async function replay(){setBusy(true);setError('');setResult(null);try{setResult(await evaluationPost(`${apiBase}/evaluation/replay`,{dataset_item_ids:selected}));}catch(e){setError(String(e));}finally{setBusy(false);}}
  return <section>
    <div className="evaluationToolbar"><fieldset className="evaluationFilterFields" disabled={busy}><label>数据集 split<select value={split} onChange={e=>{setSplit(e.target.value);setOffset(0);}}><option value="">全部 split</option><option value="dev">dev · 开发集</option><option value="test">test · 测试集</option></select></label><label>模块<select value={module} onChange={e=>{setModule(e.target.value);setOffset(0);}}><option value="">全部模块</option>{Object.entries(moduleLabels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label></fieldset>
      <div className="evaluationActions"><span>已选 {selected.length} 条</span><div><button disabled={busy||loading} onClick={()=>setReload(x=>x+1)}>刷新</button><button className="evaluationPrimary" disabled={busy||!selected.length} onClick={replay}>{busy?'重放中…':'重放所选条目'}</button></div></div>
    </div>{error&&<p role="alert" className="evaluationError">{error}</p>}
    {loading?<p className="evaluationEmpty">加载数据集中…</p>:<><div className="evaluationTableWrap"><table><thead><tr><th>选择</th><th>数据集 split</th><th>模块</th><th>source_case_id</th><th>创建时间</th></tr></thead><tbody>{items.map(item=><tr className="evaluationClickableRow" tabIndex={0} aria-label={`数据集条目 ${item.id} 详情`} key={item.id} onClick={()=>setDetail(item)} onKeyDown={e=>{if(e.target===e.currentTarget&&(e.key==='Enter'||e.key===' ')){e.preventDefault();setDetail(item);}}}>
      <td onClick={e=>e.stopPropagation()}><input aria-label={`选择数据集 ${item.id}`} type="checkbox" disabled={busy} checked={selected.includes(item.id)} onChange={e=>setSelected(current=>e.target.checked?[...current,item.id]:current.filter(id=>id!==item.id))}/></td><td><span className="evaluationBadge">{item.dataset_split}</span></td><td>{moduleLabels[item.module_key]||item.module_key}</td><td className="evaluationId">{item.source_case_id}</td><td>{new Date(item.created_at).toLocaleString()}</td>
    </tr>)}</tbody></table></div>{!items.length&&<p className="evaluationEmpty">暂无符合条件的数据集条目</p>}</>}
    <div className="evaluationPagination"><span>每页 10 条 · 第 {offset/10+1} 页</span><div><button disabled={busy||loading||offset===0} onClick={()=>setOffset(offset-10)}>上一页</button><button disabled={busy||loading||!more} onClick={()=>setOffset(offset+10)}>下一页</button></div></div>
    {result&&<section className="evaluationResult"><h2>重放结果</h2><p>{result.complete?'批次评分完成':'批次未完整评分，请检查错误条目'} · 已评分 {result.scored_count}/{result.requested_count} · 平均分 {result.average_score??'—'}</p><pre>{JSON.stringify(result,null,2)}</pre></section>}
    {detail&&<div className="evaluationOverlay" onClick={()=>setDetail(null)}><aside role="dialog" aria-label="数据集详情" onClick={e=>e.stopPropagation()}><button className="evaluationClose" onClick={()=>setDetail(null)}>关闭详情</button><h2>数据集详情</h2><p>{moduleLabels[detail.module_key]} · {detail.dataset_split}</p><p className="evaluationId">source_case_id · {detail.source_case_id}</p>{[['input_payload','固定输入'],['golden_output','Golden Answer'],['replay_context','重放上下文'],['tags','标签']].map(([key,label])=><section key={key}><h3>{label}</h3><pre>{JSON.stringify(detail[key],null,2)}</pre></section>)}</aside></div>}
  </section>;
}
