import {studies,runs,monitor} from '../api/research.js';
export const store={studies:[],runs:[],trials:[],studyId:0,runId:0,trialId:0,pendingLooks:0,rows:[]};
export async function load(){[store.studies,store.runs,store.trials]=await Promise.all([studies.list(),runs.list(),monitor.trials()]);if(!store.studyId)store.studyId=store.studies[0]?.id||0;}
export async function loadRows(){store.rows=store.studyId?await studies.rows(store.studyId):[];}
