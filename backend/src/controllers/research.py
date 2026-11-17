from fastapi import HTTPException
from src.services import studies,runs,monitor
# Controllers keep endpoint contracts separate from persistence and calculation.
def overview(db): return {'studies':studies.list_studies(db),'runs':runs.list_runs(db)}
def create_study(db,data,user): return studies.create(db,data,user)
def list_rows(db,id): return studies.observations(db,id)
def save_row(db,id,data,user,row_id=None): return studies.save_observation(db,id,data,user,row_id)
def delete_row(db,id,row_id,user): return studies.delete_observation(db,id,row_id,user)
def create_run(db,data,user): return runs.create(db,data,user)
def review_run(db,id,data,user): return runs.approve(db,id,data,user)
def start_trial(db,data,user):
    try: return monitor.create_trial(db,data.study_id,user)
    except LookupError as e: raise HTTPException(404,str(e))
    except ValueError as e: raise HTTPException(409,str(e))
def list_trials(db,study_id=None): return monitor.list_trials(db,study_id)
def get_trial(db,id):
    try: return monitor.get_trial(db,id)
    except LookupError as e: raise HTTPException(404,str(e))
def request_look(db,id,data,user):
    try: return monitor.create_look(db,id,data.request_key,user)
    except LookupError as e: raise HTTPException(404,str(e))
    except ValueError as e: raise HTTPException(409,str(e))
def resume_look(db,id,user):
    try: return monitor.resume_look(db,id,user)
    except LookupError as e: raise HTTPException(404,str(e))
def seal_trial(db,id,user):
    try: return monitor.seal_trial(db,id,user)
    except LookupError as e: raise HTTPException(404,str(e))
    except ValueError as e: raise HTTPException(409,str(e))
