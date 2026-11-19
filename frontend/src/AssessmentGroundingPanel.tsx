import type {Citation} from './talentRunState';
import './assessment-grounding.css';

type Anchor = {evidence_id:string; model_ref:string; candidate_id:string; quote:string; citation_url:string};
type Claim = {claim_id:string; text:string; kind:string; candidate_ids:string[]; anchor_ids:string[]};
type Review = {claim_id:string; verdict:string; reason:string; reason_code:string};
type ChecklistItem = {item_id:string; question:string; expected_material?:string; anchor_ids?:string[]; trigger_details?:string[]};
type Snapshot = {schema_version:'1.0'; status:string; summary:{supported:number;blocked:number;errors:number};
  anchors:Anchor[]; claims:Claim[]; reviews:Review[];
  verification:{candidates:Record<string,{items:ChecklistItem[]; technical_actions:ChecklistItem[];omitted_count:number}>}};

export function readableGrounding(value?:Record<string,unknown>|null): value is Record<string,unknown> & Snapshot {
  const object = (row:unknown):row is Record<string,unknown> => !!row && typeof row==='object' && !Array.isArray(row);
  const item = (row:unknown) => object(row) && typeof row.item_id==='string' && typeof row.question==='string'
    && (row.anchor_ids===undefined || Array.isArray(row.anchor_ids));
  return value?.schema_version === '1.0' && ['complete','partial','degraded'].includes(String(value.status))
    && Array.isArray(value.anchors) && value.anchors.every(row=>object(row)&&typeof row.evidence_id==='string'&&typeof row.quote==='string'&&typeof row.citation_url==='string') && Array.isArray(value.claims)
    && value.claims.every(row=>object(row)&&typeof row.claim_id==='string'&&typeof row.text==='string'&&Array.isArray(row.anchor_ids))
    && Array.isArray(value.reviews) && !!value.summary && typeof value.summary === 'object'
    && value.reviews.every(row=>object(row)&&typeof row.claim_id==='string'&&typeof row.verdict==='string'&&typeof row.reason==='string')
    && !!value.verification && typeof value.verification === 'object'
    && object((value.verification as Snapshot['verification']).candidates)
    && Object.values((value.verification as Snapshot['verification']).candidates).every(group=>object(group)
      && Array.isArray(group.items)&&group.items.every(item)&&Array.isArray(group.technical_actions)&&group.technical_actions.every(item));
}

export function groundingLabel(value?:Record<string,unknown>|null) {
  if(!value || !Object.keys(value).length) return '历史报告，未经过本版结论审查';
  if(!readableGrounding(value)) return '当前客户端无法展示本版审查信息';
  return `${value.status==='complete'?'依据审查完成':value.status==='partial'?'依据审查部分完成':'依据审查降级'} · 材料陈述仍需核实`;
}

export function AssessmentGroundingPanel({snapshot,onCitation}:{snapshot?:Record<string,unknown>|null;onCitation:(value:Citation)=>void}) {
  if(!readableGrounding(snapshot)) return <p className="groundingNotice">{groundingLabel(snapshot)}</p>;
  const anchors = new Map(snapshot.anchors.map(row=>[row.evidence_id,row]));
  const links = (ids:string[]=[]) => ids.map(id=>{
    const row=anchors.get(id);
    return row ? <button type="button" className="groundingSource" key={id} onClick={()=>onCitation({ref:row.model_ref,candidate:row.candidate_id,quote:row.quote,url:row.citation_url})}>查看原文 {row.model_ref}</button> : null;
  });
  const rejected=snapshot.reviews.filter(row=>row.verdict!=='supported');
  return <section className={`groundingPanel ${snapshot.status}`} aria-label="结论依据与待核实事项">
    <div className="groundingSummary"><b>结论依据审查</b><span>通过 {snapshot.summary.supported} · 拦截 {snapshot.summary.blocked} · 错误 {snapshot.summary.errors}</span></div>
    {Object.entries(snapshot.verification.candidates).map(([candidate,group])=><details key={candidate}>
      <summary>{candidate} · 待核实 {group.items.length}{group.omitted_count?`（另有 ${group.omitted_count} 项）`:''} · 技术事项 {group.technical_actions.length}</summary>
      {group.items.length?group.items.map(row=><article key={row.item_id}><p>{row.question}</p>{row.trigger_details?.map((text,index)=><p key={index}><small>待核实记录（非已确认事实）：{text}</small></p>)}<small>{row.expected_material}</small><div>{links(row.anchor_ids)}</div></article>):<p>当前没有待核实事项。</p>}
      {group.technical_actions.map(row=><p className="groundingNotice" key={row.item_id}>{row.question}</p>)}
    </details>)}
    <details><summary>查看命题审查记录（{snapshot.reviews.length}）</summary>
      {snapshot.reviews.map(review=>{
        const claim=snapshot.claims.find(row=>row.claim_id===review.claim_id);
        const labels:Record<string,string>={supported:'原文支持',contradicted:'原文反驳',insufficient:'依据不足',conflicting:'材料冲突',error:'暂未完成'};
        return <article key={review.claim_id}><b>{labels[review.verdict]||'未知状态'}</b><p>{claim?.text}</p><small>{review.reason}</small><div>{links(claim?.anchor_ids)}</div></article>;
      })}
      {!snapshot.reviews.length&&<p>本轮没有可送审的事实命题。</p>}
    </details>
    {!!rejected.length&&<p className="groundingNotice">被拦截或暂未审查的命题未作为正式结论发布。</p>}
  </section>;
}
