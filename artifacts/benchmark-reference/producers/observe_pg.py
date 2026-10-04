"""Outside frozen producers: sampled PostgreSQL resource evidence around the full matrix."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,subprocess,time
ROOT=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--project',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--stop-file',type=Path,required=True);a=p.parse_args()
assert a.project.startswith('actiongate-gpu-')
assert not a.stop_file.exists()
code=r'''set -eu
for f in memory/memory.usage_in_bytes memory/memory.max_usage_in_bytes memory/memory.limit_in_bytes memory/memory.failcnt memory/memory.oom_control pids/pids.current pids/pids.max memory.current memory.peak memory.max memory.events pids.current pids.peak pids.max; do
  if [ -f "/sys/fs/cgroup/$f" ]; then
    printf 'CGROUP %s=' "$f"
    tr '\n' ';' < "/sys/fs/cgroup/$f"
    printf '\n'
  fi
done
psql -X -qAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT json_build_object('connections_total',count(*),'client_connections',count(*) FILTER (WHERE backend_type='client backend'),'active_clients',count(*) FILTER (WHERE backend_type='client backend' AND state='active'),'idle_clients',count(*) FILTER (WHERE backend_type='client backend' AND state='idle'),'max_connections',current_setting('max_connections')::int) FROM pg_stat_activity"
'''
producer=Path(__file__).read_bytes();report={'status':'running','project':a.project,'started_at':datetime.now(timezone.utc).isoformat(),'producer_source_sha256':hashlib.sha256(producer).hexdigest(),'sampling_interval_seconds':5,'scope':'Actual PostgreSQL container cgroup readings and pg_stat_activity counts. Connection/PID maxima are sampled observations, not an asserted continuous high-water mark. Cgroup memory peak is container-lifetime, includes earlier startup/preflight. Each sample creates one short local SQL connection and a short docker-exec shell; counts include those observer processes and that SQL connection. No inference or configuration mutations.','samples':[],'errors':[]}
a.output.parent.mkdir(parents=True,exist_ok=True)
try:
 while not a.stop_file.exists():
  began=time.monotonic();stamp=datetime.now(timezone.utc).isoformat()
  result=subprocess.run(['docker','exec',a.project+'-postgres-1','sh','-c',code],capture_output=True,text=True,timeout=20)
  if result.returncode:
   report['errors'].append({'at':stamp,'error_type':'SampleCommandFailed','exit_code':result.returncode})
  else:
   fields={};connections=None
   for line in result.stdout.splitlines():
    if line.startswith('CGROUP '):
     name,value=line[7:].split('=',1);fields[name]=value.rstrip(';')
    elif line.startswith('{'):connections=json.loads(line)
   report['samples'].append({'at':stamp,'sample_wall_seconds':round(time.monotonic()-began,4),'cgroup':fields,'postgres':connections})
  a.output.write_text(json.dumps(report,indent=2)+'\n')
  for tick in range(max(0,int((5-(time.monotonic()-began))*5))):
   if a.stop_file.exists():break
   time.sleep(.2)
finally:
 report['ended_at']=datetime.now(timezone.utc).isoformat();report['producer_source_stable']=Path(__file__).read_bytes()==producer
 report['status']='passed' if report['samples'] and not report['errors'] and report['producer_source_stable'] else 'failed'
 report['sampled_max_connections_total']=max((s['postgres']['connections_total'] for s in report['samples'] if s['postgres']),default=None)
 report['sampled_max_client_connections']=max((s['postgres']['client_connections'] for s in report['samples'] if s['postgres']),default=None)
 for label,paths in {'memory_current_bytes':['memory/memory.usage_in_bytes','memory.current'],'memory_lifetime_peak_bytes':['memory/memory.max_usage_in_bytes','memory.peak'],'pids_current':['pids/pids.current','pids.current']}.items():
  report['observed_max_'+label]=max((int(s['cgroup'][k]) for s in report['samples'] for k in paths if k in s['cgroup']),default=None)
 a.output.write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('samples','scope')}))
