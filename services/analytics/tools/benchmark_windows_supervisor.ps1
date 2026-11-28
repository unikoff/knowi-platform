
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$AcceptedBuildManifest,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$AcceptBuildFileSha,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$AcceptDriverSha,
    [Parameter(Mandatory=$true)][string]$FileFreeze,
    [Parameter(Mandatory=$true)][string]$NewEvidenceFolder,
    [ValidatePattern('^[0-9a-f]{40}$')][string]$ExpectedHeadSha,
    [ValidateSet('smoke','full')][string]$Mode='full',
    [ValidateSet('a015','a016')][string]$AnalyticsSchema='a015',
    [ValidateSet(10,15,25,50)][int[]]$Stages=@(10,15,25,50)
)
if($Mode -eq 'smoke' -and ($Stages.Count -ne 1 -or $Stages[0] -ne 50)){
 throw 'canonical smoke mode requires -Stages 50'
}
if($env:ANALYTICS_BENCHMARK_GO -ne '1'){throw 'live 607-G requires the existing explicit GO and resource slot'}
$rootItem=Get-Item -LiteralPath (Get-Location)
$branch=git branch --show-current
if($LASTEXITCODE -ne 0 -or $branch -ne 'codex/analytics-windows-v1'){
 throw 'benchmark requires the designated CORP-598 worktree branch'
}
$headSha=git rev-parse --verify 'HEAD^{commit}'
if($LASTEXITCODE -ne 0 -or $headSha -notmatch '^[0-9a-f]{40}$'){
 throw 'cannot verify the exact benchmark Git HEAD'
}
$head=$headSha.Substring(0,8)
if($ExpectedHeadSha){
 if($headSha -ne $ExpectedHeadSha){throw 'working HEAD differs from the approved release candidate commit'}
 git merge-base --is-ancestor 399df854 $headSha
 if($LASTEXITCODE -ne 0){throw 'approved release candidate must descend from the CORP-598 base'}
} elseif($head -ne '399df854'){
 throw 'an explicit release candidate commit is required outside the historical base'
}
$EvidenceRoot=Join-Path $rootItem.FullName 'docs/analytics/evidence-598/607/bench/rca-20261128'
if($NewEvidenceFolder -notmatch '^[a-z0-9][a-z0-9_-]{4,80}$'){throw 'new scoped evidence subdirectory required'}
$ResultsDir=Join-Path $EvidenceRoot $NewEvidenceFolder
if(Test-Path -LiteralPath $ResultsDir){throw 'benchmark evidence directory already exists'}
$FreezeFiles=Get-Content -LiteralPath $FileFreeze -Raw | ConvertFrom-Json
if(@($FreezeFiles).Count -lt 3){throw 'declared runtime freeze and instrument files required'}
foreach($file in $FreezeFiles){
  $item=Get-Item -LiteralPath $file.path -ErrorAction Stop
  if(-not $item.FullName.StartsWith($rootItem.FullName+'\',[StringComparison]::OrdinalIgnoreCase) -or
    $file.sha256 -notmatch '^[0-9a-f]{64}$' -or $item.Attributes -band [IO.FileAttributes]::ReparsePoint){
     throw 'freeze file is outside the exact source fingerprint'
  }
}

$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$false
$env:ANALYTICS_BENCHMARK_GO='1'
$env:PYTHONPATH='services/analytics'
$py='C:/Project Python/Pymentor_platform/services/analytics/.venv/Scripts/python.exe'
$cluster='21955f0b-14dc-4d97-b064-dcdf31c2bbde'
$fixture='services/analytics/tools/benchmark_windows_fixture.py'
$pg='services/analytics/tools/benchmark_windows_postgres.py'
$runner='services/analytics/tools/benchmark_activity_windows.py'
$root=(Get-Location).Path
$outdir=Join-Path $root 'services/analytics/.local/benchmark-results'
$builds=$AcceptedBuildManifest
function ProcessIsChild([int]$candidate,[int]$origin){
  for($level=0;$level -lt 8;$level++){
    if($candidate -eq $origin){return $true}
    $found=Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$candidate"
    if(-not $found -or $found.ParentProcessId -eq $candidate){return $false}
    $candidate=[int]$found.ParentProcessId
  }
  return $false
}
function ListenerTable {
  $found=@{}
  foreach($line in (netstat -ano -p TCP)){
    if($line -match '^\s*TCP\s+127\.0\.0\.1:(1804[1-6])\s+\S+\s+LISTENING\s+(\d+)\s*$'){
      $port=[int]$Matches[1]; if($found.ContainsKey($port)){throw "duplicate listener on $port"}
      $found[$port]=[int]$Matches[2]
    }
  }
  $found
}
function MeasurePhase([string]$Pair,[string]$Phase,[string]$Instance,[string]$Anchor,[int]$Learners,[string]$Mode,[int]$Generation=1){
  $folder=Join-Path $root "services/analytics/.local/local-live/$Instance"
  $owner=Get-Content -LiteralPath (Join-Path $folder 'owner.json') -Raw | ConvertFrom-Json
  if($owner.instance_id -ne $Instance -or $owner.do_not_drop -ne $true){throw 'private phase owner mismatch'}
  if((ListenerTable).Count){throw 'fixed runtime ports are unexpectedly owned'}
  $phaseProbe = (& $py -B $pg probe --cluster $cluster --instance $Instance | ConvertFrom-Json -DateKind String)
  if($LASTEXITCODE -ne 0 -or -not $phaseProbe.pid) {throw 'phase-specific PostgreSQL lock proof unavailable'}
  Write-Output ("phase-lock {0} pid={1} ms={2}" -f $Phase,$phaseProbe.pid,$phaseProbe.observed_wait_ms)
  $procs=@{}; $targetPids=@{}; $nativeCode=10
  try {
    foreach($role in 'authority','core','analytics'){
      $p=Start-Process -FilePath $py -WorkingDirectory $root -ArgumentList @('-B',$fixture,'serve','--pair',$Pair,'--phase',$Phase,'--component',$role) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $folder "run-$Generation-$role.out") -RedirectStandardError (Join-Path $folder "run-$Generation-$role.err")
      $procs[$role]=$p
    }
    $until=[DateTime]::UtcNow.AddSeconds(100)
    $endpoints=@('http://127.0.0.1:18041/health/ready','http://127.0.0.1:18042/ready','http://127.0.0.1:18043/ready','http://127.0.0.1:18044/ready','http://127.0.0.1:18045/ready')
    do {
      $ports=ListenerTable
      if($ports.Count -eq 6){
        foreach($roleKey in 'core','authority','analytics'){
          $port=@{core=18041;authority=18046;analytics=18042}[$roleKey]
          if(-not (ProcessIsChild $ports[$port] $procs[$roleKey].Id)){throw "refused unexpected $roleKey listener ownership"}
          $targetPids[$roleKey]=$ports[$port]
        }
        if(@(18042..18045 | Where-Object {$ports[$_] -ne $targetPids.analytics}).Count){throw 'inconsistent Analytics listener set'}
        $failed=@(foreach($uri in $endpoints){
          try{if((Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 3 -SkipHttpErrorCheck).StatusCode -ne 200){$uri}}catch{$uri}
        })
        if($failed.Count -eq 0){break}
      }
      foreach($process in $procs.Values){ if($process.HasExited){ throw "owned phase role exited with $($process.ExitCode)" } }
      if([DateTime]::UtcNow -ge $until){throw 'phase readiness did not become available'}
      Start-Sleep -Seconds 1
    }while($true)
    Write-Output ("ready {0} core={1} analytics={2} authority={3}" -f $Phase,$targetPids.core,$targetPids.analytics,$targetPids.authority)
    & $py -B $fixture serve --pair $Pair --phase $Phase --component catalog
    if($LASTEXITCODE -ne 0){throw 'publication was not confirmed'}
    & $py -B $fixture capture --pair $Pair --phase $Phase --generation $Generation --builds $builds --core-pid $targetPids.core --analytics-pid $targetPids.analytics --authority-pid $targetPids.authority --lock-probe $phaseProbe.pid $phaseProbe.at
    if($LASTEXITCODE -ne 0){throw 'private phase capture failed'}
    $suffix = if($Generation -eq 1){""}else{"-$Generation"}
    $manifest=Join-Path $root "services/analytics/.local/local-live/$Pair/windows-benchmark-$Phase$suffix.json"
    if($AnalyticsSchema -eq 'a016'){
      $captured=Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json -AsHashtable -DateKind String
      $effective=$captured.targets[$Phase].runtime_flags
      if(-not $effective){throw 'approved R1 flags missing from captured runtime'}
      Witness 'phase_runtime_flags' @{pair=$Pair;phase=$Phase;schema=$AnalyticsSchema;flags=$effective} | Out-Host
    }
    $artifact=Join-Path $outdir "$Pair-$Phase$suffix.json"
    Write-Output ("phase-start {0} {1}" -f $Phase,[DateTimeOffset]::UtcNow.ToString('o'))
    & $py -B $runner --stage $Learners --mode $Mode --pair $Pair --anchor $Anchor --manifest $manifest --run --phase $Phase --output $artifact
    $nativeCode=$LASTEXITCODE
    Write-Output ("phase-end {0} exit={1} {2}" -f $Phase,$nativeCode,[DateTimeOffset]::UtcNow.ToString('o'))
  } finally {
    if($targetPids.analytics){
      & $py -B 'services/analytics/tools/local_personal_runtime.py' --instance-id $Instance --stop
    }
    foreach($roleKey in 'core','authority'){
      $idToStop=$targetPids[$roleKey]
      if($idToStop -and (Get-Process -Id $idToStop -ErrorAction SilentlyContinue) -and (ProcessIsChild $idToStop $procs[$roleKey].Id)){
        Stop-Process -Id $idToStop
      }
    }
    $deadline=[DateTime]::UtcNow.AddSeconds(22)
    do {
      $remaining=ListenerTable
      if($remaining.Count -eq 0){break}
      if([DateTime]::UtcNow -ge $deadline){throw 'owned phase ports need reconciliation'}
      Start-Sleep -Seconds 1
    }while($true)
    Write-Output ("phase-ports-free {0} {1}" -f $Phase,[DateTimeOffset]::UtcNow.ToString('o'))
  }
  if($nativeCode -ne 0){Write-Output ("phase requires actual result inspection: {0}" -f $Phase)}
}
function Witness([string]$event,[hashtable]$fields=@{}) {
 $record=[ordered]@{utc=[DateTimeOffset]::UtcNow.ToString('o');event=$event}
 foreach($key in $fields.Keys){$record[$key]=$fields[$key]}
 $line=$record | ConvertTo-Json -Depth 8 -Compress
 [IO.File]::AppendAllLines($JournalFile,[string[]]@($line),[Text.UTF8Encoding]::new($false))
 Write-Output $line
}
function AssertFrozen {
 $currentHead=git rev-parse --verify 'HEAD^{commit}'
 if($LASTEXITCODE -ne 0 -or $currentHead -ne $headSha){throw 'Git HEAD changed during the approved benchmark'}
 $currentBranch=git branch --show-current
 if($LASTEXITCODE -ne 0 -or $currentBranch -ne $branch){throw 'Git branch changed during the approved benchmark'}
 if((Get-FileHash -LiteralPath $builds -Algorithm SHA256).Hash.ToLowerInvariant() -ne $AcceptBuildFileSha){throw 'frozen build manifest changed'}
 foreach($scriptFile in $FreezeFiles){
   if((Get-FileHash -LiteralPath $scriptFile.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $scriptFile.sha256){
      throw 'frozen measured source or harness changed'
   }
 }
 if((ListenerTable).Count){throw 'exclusive benchmark slot interrupted by a role listener'}
 & $py -B $pg verify-relay --cluster $cluster
 if($LASTEXITCODE -ne 0){throw 'PG relay ownership/readiness changed'}
}
function PrepPair([int]$Learners,[string]$Mode) {
 $ids=[ordered]@{Pair=[guid]::NewGuid().ToString();Baseline=[guid]::NewGuid().ToString();Shadow=[guid]::NewGuid().ToString()}
 $a=[DateTimeOffset]::UtcNow.AddMinutes(-30)
 $ids.Anchor=[DateTimeOffset]::new($a.Year,$a.Month,$a.Day,$a.Hour,[int]([Math]::Floor($a.Minute/15)*15),0,[TimeSpan]::Zero).ToString('yyyy-MM-ddTHH:mm:sszzz')
 $generate=@($fixture,'--stage',$Learners,'--mode',$Mode,'--pair',$ids.Pair,'--anchor',$ids.Anchor,'--schema',$AnalyticsSchema,'--v2-instance',$ids.Baseline,'--shadow-instance',$ids.Shadow,
    '--course','backend','sha256:c6f7958f97602d69f9421741133c9fa0bb135461c5a8993e7557c34d7c900bca','lesson-fff797e32c153a1694f78f8d',
    '--course','backend_copy','sha256:a9791ed5dc2d8c2d43f192ac6405bbb10eec9bb4f379030e92425560a81c5089','lesson-fa2f4f9cd78e77a18fb1778e')
 Witness 'pair_planned' @{pair=$ids.Pair;v2_instance=$ids.Baseline;shadow_instance=$ids.Shadow;anchor=$ids.Anchor;learners=$Learners;mode=$Mode;generate_argv=@($py,'-B')+$generate} | Out-Host
 & $py -B @generate | Out-Host
 if($LASTEXITCODE -ne 0){throw 'pair owner fixture generation failed'}
 foreach($phase in 'v2_only','shadow'){
   $stage=@($fixture,'stage','--pair',$ids.Pair,'--phase',$phase,'--schema',$AnalyticsSchema,'--cluster',$cluster)
   & $py -B @stage | Out-Host
   if($LASTEXITCODE -ne 0){throw 'isolated phase stage unavailable'}
   Witness 'phase_staged' @{pair=$ids.Pair;phase=$phase;argv=@($py,'-B')+$stage} | Out-Host
 }
 return $ids
}
function GetEvidence([string]$Pair,[string]$Phase,[string]$Source) {
 if(-not (Test-Path -LiteralPath $Source)){throw 'phase did not write evidence; reconcile owner and any facts first'}
 $copy=Join-Path $ResultsDir "$Pair-$Phase.json"
 [IO.File]::Copy($Source,$copy,$false)
 $raw=Get-Content -LiteralPath $copy -Raw | ConvertFrom-Json -DateKind String
 if($raw.pair -ne $Pair){throw 'phase artifact does not match id'}
 $sample=$raw.phases | Select-Object -First 1
 Witness 'phase_terminal' @{pair=$Pair;phase=$Phase;status=$raw.status;driver_sha256=$raw.driver_sha256;output=(Resolve-Path -LiteralPath $copy -Relative);sha256=(Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash.ToLowerInvariant();observed_seconds=$sample.measurement_seconds_observed;measurement_samples=$sample.resources.samples;reason=$raw.stop_reason;kind=$raw.stop_kind} | Out-Host
 if($raw.driver_sha256 -ne $AcceptDriverSha){throw 'phase observed an unexpected instrumentation revision'}
 return $raw
}

function ReconcilePair($plan,[bool]$shadowMustBeFresh) {
 $env:CORP607_PHASE_V2=$plan.Baseline
 $env:CORP607_PHASE_SHADOW=$plan.Shadow
 $env:CORP607_REQUIRE_FRESH_SHADOW=if($shadowMustBeFresh){'1'}else{'0'}
 $env:CORP607_EXPECTED_ANALYTICS_SCHEMA=$AnalyticsSchema
 $savedPyPath=$env:PYTHONPATH
 $probe=@'
from uuid import UUID
import json, os, sqlite3
from benchmark_windows_postgres import cluster_connection, pg_connect
from pymentor_analytics.adapters.privacy_authority import local_server
import shadow_607_target as target
data,_,guard=cluster_connection(UUID("21955f0b-14dc-4d97-b064-dcdf31c2bbde"))
for phase,key in (("v2_only","CORP607_PHASE_V2"),("shadow","CORP607_PHASE_SHADOW")):
 folder=local_server._instance_dir(str(UUID(os.environ[key])))
 o=json.loads(local_server._read(folder,"corp607-pending.json",4096))
 if o["instance_id"]!=str(UUID(os.environ[key])) or o["owner"]!="607":
  raise RuntimeError("phase sentinel owner mismatch")
 with guard(data),pg_connect(data["password"],o["analytics"],options="-c default_transaction_read_only=on") as conn:
  target.observe_identity(conn,o["analytics"],o["analytics_owner"],o["sentinel"]["analytics"])
  facts=dict(conn.execute("SELECT accepted_event #>> '{event,type}',count(*) FROM analytics.activity_facts GROUP BY 1").fetchall())
  schema=conn.execute("SELECT version_num FROM public.alembic_version").fetchone()[0]
  if schema != os.environ["CORP607_EXPECTED_ANALYTICS_SCHEMA"]:
   raise RuntimeError("actual phase Alembic revision differs from the approved schema")
  capture=None
  if schema=="a016":
   triggers=dict(conn.execute("""SELECT tgname,tgenabled FROM pg_trigger WHERE NOT tgisinternal AND
        ((tgrelid='analytics.activity_facts'::regclass AND tgname='window_backfill_fact_changed') OR
         (tgrelid='analytics.subject_fences'::regclass AND tgname='window_backfill_subject_deleted'))""").fetchall())
   journal_count, measured_count=conn.execute("""SELECT count(*),count(measured_start)
       FROM analytics.window_backfill_journal""").fetchone()
   gate_open=conn.execute("SELECT NOT v2_closed FROM analytics.window_backfill_gate WHERE singleton").fetchone()==(True,)
   if (set(triggers)!={"window_backfill_fact_changed","window_backfill_subject_deleted"}
       or any(value not in {"A","O"} for value in triggers.values()) or not gate_open
       or journal_count!=sum(facts.values())
       or measured_count!=sum(facts.get(kind,0) for kind in ("activity_segment","tool_segment"))):
    raise RuntimeError("a016 capture evidence differs from the admitted facts or the v2 gate is closed")
   capture=dict(triggers=triggers,journal_rows=journal_count,measured_journal_rows=measured_count,v2_gate_open=gate_open)
  stats=conn.execute("""SELECT count(*),coalesce(sum(ms),0),min(revision),max(revision)
       FROM analytics.student_activity_windows
       CROSS JOIN LATERAL (SELECT sum((r->>'active_ms')::int) AS ms FROM jsonb_array_elements(stats) r) s""").fetchone()
  slots=conn.execute("""SELECT activity_date::text,slot_no,count(*),SUM(ms)
       FROM analytics.student_activity_windows
       CROSS JOIN LATERAL (SELECT sum((r->>'active_ms')::int) AS ms FROM jsonb_array_elements(stats) r) s
       GROUP BY 1,2 ORDER BY 1,2""").fetchall()
  controls=dict(conn.execute("SELECT phase,count(*) FROM analytics.student_activity_window_control GROUP BY phase").fetchall())
  applied=conn.execute("SELECT applied_through FROM analytics.privacy_state WHERE singleton").fetchone()
 journal=local_server._file(folder,"authority.sqlite")
 with sqlite3.connect(journal.as_uri()+"?mode=ro",uri=True) as sqlite:
  high=sqlite.execute("SELECT high_watermark FROM authority_meta WHERE singleton=1").fetchone()[0]
 print(json.dumps(dict(phase=phase,instance=o["instance_id"],sentinel="verified",schema=schema,a016_capture=capture,
     facts=facts,windows=stats[0],
     window_ms=int(stats[1]),revision=list(stats[2:]),slots=[list(row[:3])+[int(row[3])] for row in slots],
     control_phases=controls,privacy_applied=applied[0] if applied else None,authority_high_watermark=high)))
 if phase=="shadow" and os.environ.get("CORP607_REQUIRE_FRESH_SHADOW")=="1" and (facts or stats[0] or controls):
  raise RuntimeError("shadow database is not fresh")
'@
 try {
   $env:PYTHONPATH='services/analytics/tools;services/analytics'
   $answers=@(& $py -B -c $probe)
   if($LASTEXITCODE -ne 0 -or $answers.Count -ne 2){throw 'phase SQL and authority reconciliation failed'}
   foreach($answer in $answers){
      $observed=$answer | ConvertFrom-Json -AsHashtable
      Witness 'phase_read_only_reconcile' @{pair=$plan.Pair;observation=$observed} | Out-Host
   }
 } finally {
   $env:PYTHONPATH=$savedPyPath
 }
}

function RunPair([int]$Learners,[string]$Mode){
 AssertFrozen | Out-Host
 $plan=PrepPair $Learners $Mode
 $observed=@{}
 foreach($phase in 'v2_only','shadow'){
   AssertFrozen | Out-Host
   $instance=if($phase -eq 'v2_only'){$plan.Baseline}else{$plan.Shadow}
   $manifest=Join-Path $root "services/analytics/.local/local-live/$($plan.Pair)/windows-benchmark-$phase.json"
   $raw=Join-Path $outdir "$($plan.Pair)-$phase.json"
   Witness 'phase_invocation' @{pair=$plan.Pair;phase=$phase;runner_argv=@($py,'-B',$runner,'--stage',$Learners,'--mode',$Mode,'--pair',$plan.Pair,'--anchor',$plan.Anchor,'--manifest',$manifest,'--run','--phase',$phase,'--output',$raw)} | Out-Host
   MeasurePhase $plan.Pair $phase $instance $plan.Anchor $Learners $Mode | Out-Host
   $observed[$phase]=GetEvidence $plan.Pair $phase $raw
   ReconcilePair $plan ($phase -eq 'v2_only')
   AssertFrozen | Out-Host
   if($observed[$phase].status -eq 'UNRUN' -and $observed[$phase].stop_reason -match '(resource|commit headroom) floor reached'){
      throw 'global resource stop after guarded reconciliation'
   }
 }
 if(@($observed.Values | Where-Object {$_.status -ne 'OBSERVED'}).Count){
   $status=if(@($observed.Values | Where-Object {$_.status -eq 'FAIL'}).Count){'FAIL'}else{'UNRUN'}
   Witness 'pair_terminal' @{pair=$plan.Pair;status=$status;stage=$Learners;mode=$Mode;comparison='not_available'} | Out-Host
   return @{pair=$plan;status=$status;evidence=$observed}
 }
 $base=Join-Path $outdir "$($plan.Pair)-v2_only.json"
 $shadow=Join-Path $outdir "$($plan.Pair)-shadow.json"
 $out=Join-Path $outdir "$($plan.Pair)-comparison.json"
 & $py -B $runner compare $base $shadow --output $out | Out-Host
 if(-not (Test-Path -LiteralPath $out)){throw 'complete phase records did not produce comparison'}
 $verdict=GetEvidence $plan.Pair 'comparison' $out
 Witness 'pair_terminal' @{pair=$plan.Pair;status=$verdict.status;stage=$Learners;mode=$Mode;compare_argv=@($py,'-B',$runner,'compare',$base,$shadow,'--output',$out)} | Out-Host
 return @{pair=$plan;status=$verdict.status;evidence=$observed;comparison=$verdict}
}

$ErrorActionPreference='Stop'
AssertFrozen | Out-Host
New-Item -ItemType Directory -Path $ResultsDir | Out-Null
$JournalFile=Join-Path $ResultsDir 'run-events.ndjson'
Witness 'series_start' @{head=$head;head_sha=$headSha;expected_head_sha=$ExpectedHeadSha;branch=$branch;mode=$Mode;stages=$Stages;analytics_schema=$AnalyticsSchema;build_manifest=$AcceptedBuildManifest;build_manifest_sha256=$AcceptBuildFileSha;driver_sha256=$AcceptDriverSha;freeze_source=$FileFreeze} | Out-Host
try {
 $stageVerdicts=[ordered]@{}
 foreach($loadStage in $Stages){
   $stability=if($Mode -eq 'full'){3}else{1}
   $earned=0
   $sensorRetries=0
   while($earned -lt $stability){
      $pair=RunPair $loadStage $Mode
      if($pair.status -eq 'PASS'){
         $earned++
         continue
      }
      $unrun=@($pair.evidence.Values | Where-Object {$_.status -eq 'UNRUN'})
      $recognizedTransient=$unrun.Count -gt 0 -and @($unrun | Where-Object {
        $_.stop_reason -notmatch '^PostgreSQL CPU/RSS (sample timed out|Docker exec unavailable)$'
      }).Count -eq 0
      if($pair.status -eq 'UNRUN' -and $recognizedTransient -and $sensorRetries -eq 0){
         AssertFrozen | Out-Host
         $sensorRetries++
         Witness 'fresh_pair_retry' @{stage=$loadStage;prior_pair=$pair.pair.Pair;why='bounded transient read-only sensor; prior PG outcome reconciled'} | Out-Host
         continue
      }
      break
   }
   $stageState=if($earned -eq $stability){'PASS'}else{$pair.status}
   $stageVerdicts[[string]$loadStage]=$stageState
   Witness 'stage_terminal' @{stage=$loadStage;status=$stageState;observed_successful_pairs=$earned;required_successful_pairs=$stability} | Out-Host
 }
 Witness 'series_completed' @{stages=$stageVerdicts} | Out-Host
} catch {
 Witness 'series_stopped' @{error_class=$_.Exception.GetType().Name;fixed_ports_free=((ListenerTable).Count -eq 0)} | Out-Host
 throw
}
