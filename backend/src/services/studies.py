from fastapi import HTTPException
from sqlalchemy import select,func
from src.models.study import Study
from src.models.observation import Observation
from src.services import monitor
from src.services.domain_validation import validate_values
from src.services.audits import log
from src.utils.serializers import record

def list_studies(db):
    data=[]
    for s in db.scalars(select(Study).order_by(Study.id)):
        x=record(s);x['row_count']=db.scalar(select(func.count()).select_from(Observation).where(Observation.study_id==s.id));data.append(x)
    return data

def create(db,data,user):
    if db.scalar(select(Study).where(Study.name==data.name)): raise HTTPException(409,'批次名称已存在')
    s=Study(**data.model_dump());db.add(s);log(db,user,'study.create',data.name);db.commit();return record(s)

def require(db,id):
    s=db.get(Study,id)
    if not s: raise HTTPException(404,'业务批次不存在')
    return s

def observations(db,id):
    require(db,id);return [record(x) for x in db.scalars(select(Observation).where(Observation.study_id==id).order_by(Observation.id))]

def save_observation(db,id,data,user,row_id=None):
    require(db,id)
    try: values=validate_values(data.values)
    except (ValueError,TypeError) as e: raise HTTPException(422,str(e))
    # Registering a brand-new pair never touches evidence already included, so
    # it takes no monitoring lock and is never blocked by a look.  Editing or
    # deleting may change an included pair, so it locks the trial first and the
    # observation second -- the same order a look uses (trial then all
    # observations), which prevents deadlocks and prevents an edit from
    # slipping between a look's completeness check and its evidence commit.
    if row_id: monitor.lock_open_trial(db,id)
    other=db.scalar(select(Observation).where(Observation.study_id==id,Observation.source_key==data.source_key))
    if other and other.id!=row_id: raise HTTPException(409,'源记录编号重复')
    sealed=None
    if row_id:
        row=db.scalar(select(Observation).where(Observation.id==row_id).with_for_update())
        if not row or row.study_id!=id: raise HTTPException(404,'记录不存在')
        changed=row.source_key!=data.source_key or monitor.values_digest(row.values)!=monitor.values_digest(values)
        if changed:
            detail=(f'#{row.source_key} 改名为 #{data.source_key}'
                    if row.source_key!=data.source_key
                    else f'#{row.source_key} 数值被修改')
            sealed=monitor.seal_study_trial(
                db,id,row_id,'included_observation_changed',detail,user=user,
            )
        row.source_key=data.source_key;row.values=values
    else:
        if len(observations(db,id))>=5000: raise HTTPException(422,'小型研究最多5000行')
        row=Observation(study_id=id,source_key=data.source_key,values=values);db.add(row)
    log(db,user,'observation.save',f'{id}/{data.source_key}')
    db.commit()
    result=record(row)
    if sealed: result['monitor_seal']=sealed
    return result

def delete_observation(db,id,row_id,user):
    require(db,id)
    monitor.lock_open_trial(db,id)
    row=db.scalar(select(Observation).where(Observation.id==row_id).with_for_update())
    if not row or row.study_id!=id: raise HTTPException(404,'记录不存在')
    sealed=monitor.seal_study_trial(
        db,id,row_id,'included_observation_deleted',f'#{row.source_key} 删除',user=user,
    )
    db.delete(row);log(db,user,'observation.delete',row_id);db.commit()
    result={'deleted':True}
    if sealed: result['monitor_seal']=sealed
    return result
