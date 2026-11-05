"""Compare entity-level reference observations with calculated takeoff JSON, locally."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path

DIMENSIONS={'length_mm','height_mm','depth_mm'}
FACTS={'count','repeat_count','parent_id','instance_key','unit','method','name','space','excluded_reason'}


def _finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def compare_benchmark(expected, actual):
    """Expected {cases:[{id,bucket?,status?,...facts}], tolerances:{dimension_mm,quantity}}.

    Compare stable IDs, never match by position/name or by aggregate amounts. The
    benchmark measures agreement with explicit references, not drawing accuracy.
    """
    cases=expected.get('cases',expected.get('expected',[]))
    if not isinstance(cases,list) or not cases or not all(isinstance(c,dict) and c.get('id') for c in cases):
        raise ValueError('基准cases必须是带稳定id的对象数组')
    counts=Counter(c['id'] for c in cases)
    if any(n>1 for n in counts.values()): raise ValueError('基准expected含重复id')
    tolerances={'dimension_mm':0,'quantity':.0001}
    tolerances.update(expected.get('tolerances',{}))
    if any(not _finite(tolerances[k]) or tolerances[k]<0 for k in ('dimension_mm','quantity')): raise ValueError('容差必须是有限非负数')
    entries=[]
    for bucket in ('rows','review','excluded'):
        for row in actual.get(bucket,[]): entries.append((row.get('id'),bucket,row))
    actual_counts=Counter(e[0] for e in entries)
    duplicate_ids=sorted(str(k) for k,n in actual_counts.items() if n>1)
    found={key:(bucket,row) for key,bucket,row in entries if key is not None}
    wanted={c['id'] for c in cases}
    missing=sorted(wanted-found.keys()); unexpected=sorted(str(k) for k in found.keys()-wanted)
    metrics=dict(expected_entities=len(cases),actual_entities=len(entries),matched_entities=0,
                 dimension_checks=0,dimension_matches=0,quantity_checks=0,quantity_matches=0,
                 classification_checks=0,classification_matches=0,status_checks=0,status_matches=0,
                 factual_checks=0,factual_matches=0,false_confirmed=0,genuine_pending=0,unexpected_pending=0)
    details=[]
    for case in cases:
        cid=case['id']
        if cid not in found: details.append(dict(id=cid,issues=['missing'])); continue
        bucket,row=found[cid]; metrics['matched_entities']+=1; issues=[]
        if actual_counts[cid]>1: issues.append('duplicate_id')
        reference=dict(case.get('factual_fields',{})); reference.update({k:v for k,v in case.items() if k!='factual_fields'})
        expected_bucket=reference.get('bucket')
        if expected_bucket is None:
            expected_bucket={'待复核':'review','不单列':'excluded','依据齐全':'rows','人工确认':'rows'}.get(reference.get('status'))
        if expected_bucket is not None:
            if expected_bucket not in {'rows','review','excluded'}: raise ValueError('基准bucket须为rows/review/excluded')
            metrics['status_checks']+=1
            if bucket==expected_bucket: metrics['status_matches']+=1
            else: issues.append(dict(field='bucket',expected=expected_bucket,actual=bucket))
            if expected_bucket=='review' and bucket=='rows': metrics['false_confirmed']+=1
            if expected_bucket=='review' and bucket=='review': metrics['genuine_pending']+=1
            if expected_bucket=='rows' and bucket=='review': metrics['unexpected_pending']+=1
        for field in DIMENSIONS|FACTS|{'product_type','status','quantity','measured_quantity'}:
            if field not in reference: continue
            target=reference[field]; got=row.get(field)
            category=('dimension' if field in DIMENSIONS else 'quantity' if field in {'quantity','measured_quantity'}
                      else 'classification' if field=='product_type' else 'status' if field=='status' else 'factual')
            metrics[f'{category}_checks']+=1
            if category in {'dimension','quantity'} and target is not None:
                tolerance=tolerances['dimension_mm' if category=='dimension' else 'quantity']
                equal=_finite(target) and _finite(got) and abs(target-got)<=tolerance+1e-10
            else: equal=got==target
            if equal: metrics[f'{category}_matches']+=1
            else: issues.append(dict(field=field,expected=target,actual=got))
        details.append(dict(id=cid,bucket=bucket,issues=issues))
    return dict(schema_version=1,name=expected.get('name',''),reference_kind=expected.get('reference_kind','explicit_reference'),
                passed=not missing and not unexpected and not duplicate_ids and all(not d['issues'] for d in details),
                metrics=metrics,tolerances=tolerances,missing=missing,unexpected=unexpected,duplicates=duplicate_ids,details=details,
                note='仅衡量逐实体参考字段的一致性；合成样例不代表真实识图准确率，汇总金额相同不构成通过。')


def run_benchmark(manifest_path, actual_path, output_path=None):
    manifest=json.loads(Path(manifest_path).read_text(encoding='utf-8-sig'))
    actual=json.loads(Path(actual_path).read_text(encoding='utf-8-sig'))
    report=compare_benchmark(manifest,actual)
    if output_path:
        path=Path(output_path); path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest'); parser.add_argument('actual'); parser.add_argument('--out')
    args=parser.parse_args(); report=run_benchmark(args.manifest,args.actual,args.out)
    print(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
