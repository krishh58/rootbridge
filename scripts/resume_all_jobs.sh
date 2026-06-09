#!/usr/bin/env bash
# ============================================================
# RootBridge — Resume All Background Jobs
# ============================================================
# Run this any time to restart any pipeline job that died.
# Safe to run while jobs are already running — each check
# skips the job if it's already active.
#
# Usage:
#   cd ~/familytree_app
#   bash scripts/resume_all_jobs.sh
#
# What this manages:
#   1. SSDI upload      — ssdm1 → ssdm2 → ssdm3 sequential
#   2. BIRLS upload     — veterans records
#   3. CT OCR download  — Chronicling America newspaper scans
#   4. WikiTree import  — 30M genealogy profiles
#   5. IGI scraper      — FamilySearch/GeoCities GEDs via Wayback
#   6. WeRelate scraper — 325K persons via Wayback (Cloudflare bypass)
# ============================================================

set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
source "${APP_DIR}/venv/bin/activate"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

RAM_FLOOR_GB=6   # don't start a new job if available RAM is below this

is_running() {
    pgrep -f "$1" > /dev/null 2>&1
}

avail_ram_gb() {
    awk '/MemAvailable/ {printf "%.1f", $2/1048576}' /proc/meminfo
}

wait_for_ram() {
    local avail
    while true; do
        avail=$(avail_ram_gb)
        if (( $(echo "$avail >= $RAM_FLOOR_GB" | bc -l) )); then
            break
        fi
        echo -e "${RED}[RAM]${NC}   Only ${avail}GB available — waiting 60s before starting next job…"
        sleep 60
    done
}

log() { echo -e "${GREEN}[RESUME]${NC} $*"; }
skip() { echo -e "${YELLOW}[SKIP]${NC}   $* (already running)"; }
warn() { echo -e "${RED}[WARN]${NC}   $*"; }

echo ""
echo "=================================================="
echo "  RootBridge Pipeline Resume  —  $(date '+%Y-%m-%d %H:%M')"
echo "=================================================="
echo ""

# ── 1. SSDI Upload ────────────────────────────────────────
echo "1. SSDI upload (ssdm1 → ssdm2 → ssdm3)"
if is_running "build_ssdi_chunks"; then
    skip "SSDI uploader already running"
else
    # Check Oracle to see which file to resume from
    SSDI_COUNT=$(python3 - <<'PYEOF'
import boto3, os
from botocore.config import Config
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('.env'))
NS=os.environ.get('ORACLE_NAMESPACE','')
RG=os.environ.get('ORACLE_REGION','us-chicago-1')
AK=os.environ.get('ORACLE_ACCESS_KEY','')
SK=os.environ.get('ORACLE_SECRET_KEY','')
EP=f'https://{NS}.compat.objectstorage.{RG}.oraclecloud.com'
BK=os.environ.get('ORACLE_BUCKET','rootbridge-data')
c=boto3.client('s3',endpoint_url=EP,aws_access_key_id=AK,aws_secret_access_key=SK,
    config=Config(signature_version='s3v4',request_checksum_calculation='when_required'),region_name=RG)
pag=c.get_paginator('list_objects_v2')
print(sum(p['KeyCount'] for p in pag.paginate(Bucket=BK,Prefix='ssdi/')))
PYEOF
    )
    echo "   Oracle ssdi/ chunks: ${SSDI_COUNT} / 71458 total (ssdm1) + ssdm2+ssdm3"
    if [ "${SSDI_COUNT}" -ge 71458 ]; then
        log "SSDI ssdm1 COMPLETE — checking if ssdm2/ssdm3 still needed"
        # Count total expected (ssdm1+ssdm2+ssdm3 ≈ 183,000+)
        # For now just try to run ssdm2+ssdm3 — build_ssdi_chunks skips existing keys
        nohup bash -c 'source /home/krish/familytree_app/venv/bin/activate
            cd /home/krish/familytree_app
            for f in ssdm2 ssdm3; do
                echo "Starting ${f}..."
                python3 scripts/build_ssdi_chunks.py \
                    --input /mnt/h/RootBridge/2013/${f}.csv --upload \
                    >> /tmp/ssdi_upload.log 2>&1
            done
            echo "SSDI ALL DONE"' > /tmp/ssdi_nohup.log 2>&1 &
        log "Started ssdm2+ssdm3 (PID $!)"
    else
        wait_for_ram
        nohup bash -c 'source /home/krish/familytree_app/venv/bin/activate
            cd /home/krish/familytree_app
            for f in ssdm1 ssdm2 ssdm3; do
                echo "Starting ${f}..."
                python3 scripts/build_ssdi_chunks.py \
                    --input /mnt/h/RootBridge/2013/${f}.csv --upload \
                    >> /tmp/ssdi_upload.log 2>&1
            done
            echo "SSDI ALL DONE"' > /tmp/ssdi_nohup.log 2>&1 &
        log "Started SSDI ssdm1→ssdm2→ssdm3 (PID $!) — log: /tmp/ssdi_upload.log"
    fi
fi
sleep 5

# ── 2. BIRLS Upload ───────────────────────────────────────
echo ""
echo "2. BIRLS upload (veterans records)"
if is_running "build_archive_chunks.*birls"; then
    skip "BIRLS uploader already running"
else
    BIRLS_COUNT=$(python3 - <<'PYEOF'
import boto3, os
from botocore.config import Config
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('.env'))
NS=os.environ.get('ORACLE_NAMESPACE','')
RG=os.environ.get('ORACLE_REGION','us-chicago-1')
AK=os.environ.get('ORACLE_ACCESS_KEY','')
SK=os.environ.get('ORACLE_SECRET_KEY','')
EP=f'https://{NS}.compat.objectstorage.{RG}.oraclecloud.com'
BK=os.environ.get('ORACLE_BUCKET','rootbridge-data')
c=boto3.client('s3',endpoint_url=EP,aws_access_key_id=AK,aws_secret_access_key=SK,
    config=Config(signature_version='s3v4',request_checksum_calculation='when_required'),region_name=RG)
pag=c.get_paginator('list_objects_v2')
print(sum(p['KeyCount'] for p in pag.paginate(Bucket=BK,Prefix='birls/')))
PYEOF
    )
    echo "   Oracle birls/ chunks: ${BIRLS_COUNT} / 60338 total"
    if [ "${BIRLS_COUNT}" -ge 60338 ]; then
        log "BIRLS already COMPLETE (${BIRLS_COUNT}/60338)"
    else
        wait_for_ram
        nohup python3 scripts/build_archive_chunks.py \
            --source birls --upload \
            > /tmp/birls_upload.log 2>&1 &
        log "Started BIRLS (PID $!) — log: /tmp/birls_upload.log"
    fi
fi
sleep 5

# ── 3. CT OCR Download ────────────────────────────────────
echo ""
echo "3. Chronicling America OCR download (CT → AK → IN → ME → AZ → WA)"
if is_running "download_ca_ocr"; then
    skip "CA OCR downloader already running"
else
    # Regenerate batch files if /tmp was cleared
    if [ ! -f /tmp/ca_state_batches/ct.txt ]; then
        log "Regenerating state batch files..."
        python3 - <<'PYEOF'
import sys, os
sys.path.insert(0, 'scripts')
from process_ca_ocr import get_all_batches
from collections import defaultdict
from pathlib import Path
os.makedirs('/tmp/ca_state_batches', exist_ok=True)
batches = get_all_batches()
by_state = defaultdict(list)
for b in batches:
    by_state[b.split('_')[0]].append(b)
for state in ['ct','ak','in','me','az','wa']:
    Path(f'/tmp/ca_state_batches/{state}.txt').write_text('\n'.join(sorted(by_state[state])))
    print(f"  {state}: {len(by_state[state])} batches")
PYEOF
    fi
    # Find next incomplete state
    for STATE in ct ak in me az wa; do
        DONE_DIR="${APP_DIR}/data/ca_ocr_raw/${STATE}"
        if [ ! -d "${DONE_DIR}" ] || [ "$(ls -A ${DONE_DIR} 2>/dev/null | wc -l)" -eq 0 ]; then
            wait_for_ram
            nohup python3 scripts/download_ca_ocr.py \
                --batch-file /tmp/ca_state_batches/${STATE}.txt \
                > /tmp/dl_${STATE}.log 2>&1 &
            log "Started OCR download for ${STATE} (PID $!) — log: /tmp/dl_${STATE}.log"
            break
        fi
    done
fi
sleep 5

# ── 4. WikiTree Importer ──────────────────────────────────
echo ""
echo "4. WikiTree bulk importer (~30M profiles)"
if is_running "wikitree_importer"; then
    skip "WikiTree importer already running"
else
    # Check progress
    if [ -f "${APP_DIR}/data/wikitree_progress.json" ]; then
        WIKI_DONE=$(python3 -c "
import json
d=json.load(open('data/wikitree_progress.json'))
done=len([k for k,v in d.items() if v.get('done')])
print(done)
" 2>/dev/null || echo "?")
        log "WikiTree: ${WIKI_DONE}/1421 sitemaps done — resuming"
    fi
    wait_for_ram
    nohup python3 scripts/wikitree_importer.py \
        > /tmp/wikitree_import.log 2>&1 &
    log "Started WikiTree importer (PID $!) — log: /tmp/wikitree_import.log"
fi
sleep 5

# ── 5. IGI Wayback Scraper ────────────────────────────────
echo ""
echo "5. IGI/FamilySearch Wayback scraper (90 queries)"
if is_running "igi_wayback_scraper"; then
    skip "IGI scraper already running"
else
    if [ -f "${APP_DIR}/data/igi_scraper_checkpoint.json" ]; then
        QUERY_DONE=$(python3 -c "
import json
d=json.load(open('data/igi_scraper_checkpoint.json'))
print(d.get('queries_done', 0))
" 2>/dev/null || echo "?")
        log "IGI scraper: ${QUERY_DONE}/90 queries done — resuming"
    fi
    wait_for_ram
    nohup python3 scripts/igi_wayback_scraper.py \
        > /tmp/igi_scraper_stdout.log 2>&1 &
    log "Started IGI scraper (PID $!) — log: /tmp/igi_scraper.log"
fi
sleep 5

# ── 6. WeRelate Wayback Scraper ───────────────────────────
echo ""
echo "6. WeRelate Wayback scraper (~325K person pages)"
if is_running "werelate_wayback_scraper"; then
    skip "WeRelate scraper already running"
else
    if [ -f "${APP_DIR}/data/werelate_checkpoint.json" ]; then
        PAGES_DONE=$(python3 -c "
import json
d=json.load(open('data/werelate_checkpoint.json'))
print(len(d.get('cdx_pages_done', [])))
" 2>/dev/null || echo "0")
        PERSONS=$(python3 -c "
import json
d=json.load(open('data/werelate_checkpoint.json'))
print(d.get('total_inserted', 0))
" 2>/dev/null || echo "0")
        log "WeRelate: ${PAGES_DONE}/65 CDX pages done, ${PERSONS} persons — resuming"
    fi
    wait_for_ram
    nohup python3 scripts/werelate_wayback_scraper.py \
        > /tmp/werelate_scraper_stdout.log 2>&1 &
    log "Started WeRelate scraper (PID $!) — log: /tmp/werelate_scraper.log"
fi
sleep 5

# ── 7. Wikidata European Scraper ─────────────────────────
echo ""
echo "7. Wikidata pre-1800 Europeans (~60K persons, 1400s–1799)"
if is_running "wikidata_scraper"; then
    skip "Wikidata scraper already running"
else
    if [ -f "${APP_DIR}/data/wikidata_checkpoint.json" ]; then
        WD_DONE=$(python3 -c "
import json
d=json.load(open('data/wikidata_checkpoint.json'))
print(len(d.get('decades_done', [])))
" 2>/dev/null || echo "0")
        WD_INS=$(python3 -c "
import json
d=json.load(open('data/wikidata_checkpoint.json'))
print(d.get('total_inserted', 0))
" 2>/dev/null || echo "0")
        log "Wikidata: ${WD_DONE}/40 decades done, ${WD_INS} persons — resuming"
    fi
    wait_for_ram
    nohup python3 scripts/wikidata_scraper.py \
        > /tmp/wikidata_stdout.log 2>&1 &
    log "Started Wikidata scraper (PID $!) — log: /tmp/wikidata_scraper.log"
fi
sleep 5

# ── 8. Open Archives NL Scraper ───────────────────────────
echo ""
echo "8. Open Archives NL — Dutch civil records (AA-ZZ surname combos)"
if is_running "openarch_scraper"; then
    skip "OpenArch scraper already running"
else
    if [ -f "${APP_DIR}/data/openarch_checkpoint.json" ]; then
        OA_DONE=$(python3 -c "
import json
d=json.load(open('data/openarch_checkpoint.json'))
print(len(d.get('combos_done', [])))
" 2>/dev/null || echo "0")
        OA_INS=$(python3 -c "
import json
d=json.load(open('data/openarch_checkpoint.json'))
print(d.get('total_inserted', 0))
" 2>/dev/null || echo "0")
        log "OpenArch: ${OA_DONE}/676 combos done, ${OA_INS} persons — resuming"
    fi
    wait_for_ram
    nohup python3 scripts/openarch_scraper.py \
        > /tmp/openarch_stdout.log 2>&1 &
    log "Started OpenArch scraper (PID $!) — log: /tmp/openarch_scraper.log"
fi

# ── 9. GED Chunk Upload (run after SSDI settles) ──────────
echo ""
echo "7. GED Oracle chunks (Alfred routing layer)"
GED_COUNT=$(python3 - <<'PYEOF'
import boto3, os
from botocore.config import Config
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('.env'))
NS=os.environ.get('ORACLE_NAMESPACE','')
RG=os.environ.get('ORACLE_REGION','us-chicago-1')
AK=os.environ.get('ORACLE_ACCESS_KEY','')
SK=os.environ.get('ORACLE_SECRET_KEY','')
EP=f'https://{NS}.compat.objectstorage.{RG}.oraclecloud.com'
BK=os.environ.get('ORACLE_BUCKET','rootbridge-data')
c=boto3.client('s3',endpoint_url=EP,aws_access_key_id=AK,aws_secret_access_key=SK,
    config=Config(signature_version='s3v4',request_checksum_calculation='when_required'),region_name=RG)
pag=c.get_paginator('list_objects_v2')
print(sum(p['KeyCount'] for p in pag.paginate(Bucket=BK,Prefix='ged/')))
PYEOF
)
if is_running "build_ged_chunks"; then
    skip "GED uploader already running"
elif [ "${GED_COUNT}" -eq 0 ]; then
    warn "GED/ prefix still empty. Run manually when SSDI/BIRLS finish:"
    echo "        nohup python3 scripts/build_ged_chunks.py --upload > /tmp/ged_chunks.log 2>&1 &"
else
    log "GED chunks: ${GED_COUNT} already in Oracle"
fi

# ── Status Summary ────────────────────────────────────────
echo ""
echo "=================================================="
echo "  RUNNING JOBS"
echo "=================================================="
ps aux | grep -E "(build_ssdi|build_archive|download_ca_ocr|wikitree|igi_wayback|werelate_wayback|build_ged|wikidata_scraper|openarch_scraper)" \
    | grep -v grep \
    | awk '{print "  PID " $2 ": " $11 " " $12 " " $13}'

echo ""
echo "  Logs:"
echo "    SSDI:     tail -f /tmp/ssdi_upload.log"
echo "    BIRLS:    tail -f /tmp/birls_upload.log"
echo "    OCR:      tail -f /tmp/dl_ct.log  (or dl_ak, dl_in etc)"
echo "    WikiTree: tail -f /tmp/wikitree_import.log"
echo "    IGI:      tail -f /tmp/igi_scraper.log"
echo "    WeRelate: tail -f /tmp/werelate_scraper.log"
echo "    Wikidata: tail -f /tmp/wikidata_scraper.log"
echo "    OpenArch: tail -f /tmp/openarch_scraper.log"
echo "    GED:      tail -f /tmp/ged_chunks.log"
echo ""
echo "  For Claude: tell the new session 'resume rootbridge pipelines'"
echo "  and point it at: ~/.claude/projects/-home-krish/memory/project_rootbridge_pipeline_status.md"
echo "=================================================="
