# Correlation report · SIM-REPORT

**Run** ` SIM-RUN-1 `

**Status** Probable cause

## Summary

Probable origin in toy-storage: toy-storage ran out of disk space, causing write W1 to be rejected with ENOSPC. toy-orders attempted to write W1 to toy-storage; the ENOSPC error propagated, causing order O1 to fail. The trace for order O1 shows its span failing immediately after the child span for the storage write returned ENOSPC, reflecting the same propagation.

## 1. Probable origin · toy-storage

toy-storage ran out of disk space, causing write W1 to be rejected with ENOSPC.

**Evidence**

- **logs** · toy-storage rejected write W1 with ENOSPC (disk full).
  Finding ` SIM-logs-1 ` · References ` SIM-REPORT/snapshot-1/logs#1 `
- **metrics** · Disk space used reached 100% during write W1.
  Finding ` SIM-metrics-1 ` · References ` SIM-REPORT/snapshot-1/metrics#1 `

## 2. Inferred effect · toy-orders

Likely consequence of step 1.

toy-orders attempted to write W1 to toy-storage; the ENOSPC error propagated, causing order O1 to fail.

**Evidence**

- **logs** · toy-orders failed order O1 because its write W1 to toy-storage returned ENOSPC.
  Finding ` SIM-logs-2 ` · References ` SIM-REPORT/snapshot-1/logs#2 `

## 3. Inferred effect · toy-orders

Likely consequence of step 1.

The trace for order O1 shows its span failing immediately after the child span for the storage write returned ENOSPC, reflecting the same propagation.

**Evidence**

- **traces** · Order O1 span failed immediately after its child span for toy-storage write W1 returned ENOSPC.
  Finding ` SIM-traces-1 ` · References ` SIM-REPORT/snapshot-1/traces#1 `
