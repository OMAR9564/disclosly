from workers.recovery import CrashRecoveryWorker

worker = CrashRecoveryWorker()

# Test lease acquisition
run_id = "test_run_123"
acquired = worker.acquire_lease(run_id, ttl_seconds=30)
print(f"Lease acquired for {run_id}: {acquired}")

# Test duplicate acquisition (should be False since it's already locked)
acquired_again = worker.acquire_lease(run_id, ttl_seconds=30)
print(f"Duplicate lease acquired (should be False): {acquired_again}")