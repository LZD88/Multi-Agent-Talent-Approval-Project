import {describe,it,expect} from 'vitest';
import {groundingLabel,readableGrounding} from './AssessmentGroundingPanel';
import {initialRunView,reduceRunEvent,historyDetailToRunView,type RunEvent,type RunHistoryDetail} from './talentRunState';

const snapshot={schema_version:'1.0',status:'partial',summary:{supported:1,blocked:1,errors:0},anchors:[],claims:[],reviews:[],verification:{candidates:{}}};
const event=(type:string,data:Record<string,unknown>,sequence=1):RunEvent=>({type,data,sequence,run_id:'new',timestamp:'2026-11-19T08:00:00Z',event_id:`e${sequence}`,schema_version:'1.0'});

describe('assessment grounding compatibility',()=>{
  it('distinguishes old, unknown, and partially reviewed reports',()=>{
    expect(groundingLabel(null)).toContain('历史报告');
    expect(groundingLabel({schema_version:'9.0'})).toContain('无法展示');
    expect(groundingLabel(snapshot)).toContain('部分完成');
    expect(readableGrounding({schema_version:'1.0'})).toBe(false);
  });
  it('keeps the event snapshot and clears it at the next turn',()=>{
    const completed=reduceRunEvent(initialRunView,event('report.completed',{markdown:'报告',assessment_grounding:snapshot}));
    expect(completed.assessmentGrounding).toEqual(snapshot);
    const next=reduceRunEvent(completed,event('run.started',{turn_no:2},2));
    expect(next.assessmentGrounding).toBeNull();
    expect(next.report).toBe('');
  });
  it('restores optional snapshots without modifying old history',()=>{
    const detail={run_id:'r',conversation_id:'c',turn_no:1,message:'m',status:'completed',report:'报告',citations:[],trace_id:'t',steps:[],conversation_turns:[]} as RunHistoryDetail;
    expect(historyDetailToRunView(detail).assessmentGrounding).toBeUndefined();
    expect(historyDetailToRunView({...detail,assessment_grounding:snapshot}).assessmentGrounding).toEqual(snapshot);
  });
});
