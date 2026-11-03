import {useEffect, useState} from 'react';
import {Archive, ChevronLeft, ChevronRight, Database, FileCheck2, FlaskConical, LayoutGrid, MessagesSquare, Sparkles, UserSquare2, UsersRound} from 'lucide-react';
import './dashboard.css';

type Metrics = {
  employee_count:number;
  document_count:number;
  conversation_count:number;
  run_count:number;
  case_count:number;
  dataset_count:number;
};

const cards = [
  ['employee_count','员工数量',UsersRound],
  ['document_count','档案数量',Archive],
  ['conversation_count','总评估会话数',MessagesSquare],
  ['run_count','总评估轮次数',Sparkles],
  ['case_count','评测样例数',FlaskConical],
  ['dataset_count','数据集条目数',Database],
] as const;

export function Dashboard({apiBase,sidebarCollapsed,onToggleSidebar,onRecords,onChat,onEvaluation}:{apiBase:string;sidebarCollapsed:boolean;onToggleSidebar:()=>void;onRecords:()=>void;onChat:()=>void;onEvaluation:()=>void}){
  const [metrics,setMetrics]=useState<Metrics|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{
    let active=true;
    fetch(`${apiBase}/dashboard/metrics`).then(async response=>{
      if(!response.ok)throw new Error('工作台指标加载失败');
      return response.json();
    }).then(data=>{if(active)setMetrics(data);}).catch(reason=>{if(active)setError(String(reason));});
    return()=>{active=false;};
  },[apiBase]);
  return <div className={`app dashboardApp${sidebarCollapsed?' sidebarCollapsed':''}`}>
    <aside className="side"><div className="brand"><img src="/talentos-favicon.png" alt="TalentOS"/><div><b>智鉴 TalentOS</b><small>多 AGENT 人才决策平台</small></div></div><nav>
      <button className="active"><LayoutGrid/><span>工作台</span></button><button onClick={onRecords}><UserSquare2/><span>人才档案</span></button><button onClick={onChat}><Sparkles/><span>评估与推荐</span></button><button onClick={onEvaluation}><FlaskConical/><span>评测中心</span></button>
    </nav><div className="env"><div><i/><b>测试环境</b></div><small>服务正常 · V1.0</small></div><button type="button" className="sideToggle" onClick={onToggleSidebar} aria-label={sidebarCollapsed?'展开导航栏':'收起导航栏'} aria-expanded={!sidebarCollapsed}>{sidebarCollapsed?<ChevronRight/>:<ChevronLeft/>}</button></aside>
    <main><header><div><span>工作台</span><ChevronRight/><b>核心指标</b></div><div className="currentUser"><b>Will</b><span>W</span></div></header>
      <section className="dashboardContent"><div className="dashboardHeading"><div><h1>核心指标</h1><p>人才数据、评估运行与评测资产概览</p></div><FileCheck2/></div>
        {error&&<p role="alert" className="dashboardError">{error}</p>}
        <div className="dashboardMetrics">{cards.map(([key,label,Icon])=><article key={key}><span><Icon/></span><div><small>{label}</small><strong>{metrics?metrics[key]:'—'}</strong></div></article>)}</div>
      </section></main>
  </div>;
}
