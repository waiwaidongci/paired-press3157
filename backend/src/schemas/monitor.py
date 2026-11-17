from pydantic import BaseModel,Field
class MonitorTrialInput(BaseModel):
    study_id:int=Field(gt=0)
class MonitorLookInput(BaseModel):
    request_key:str|None=Field(default=None,max_length=120)
TrialInput=MonitorTrialInput
LookInput=MonitorLookInput
