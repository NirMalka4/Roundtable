# Specialist Trigger Patterns

> **Purpose**: Defines explicit trigger patterns for specialist agent invocation.
> Specialists use these patterns to determine when to run.

---

## Invocation Policies

| Policy | Behavior |
|--------|----------|
| `ALWAYS` | Run on every code review, regardless of patterns |
| `PATTERN_MANDATORY` | Run if patterns detected OR if detection is uncertain |

---

> **Scope note**: Split from the former monolithic `SpecialistPatterns.md` so the Deadlock specialist
> receives only its own trigger patterns. The orchestrator's real run/skip logic lives in
> `packages/core/src/context/coreServices.ts`; this file is the focus + policy reference.

---

## Deadlock Specialist Triggers

**File**: `Specialists/Deadlock.agent.md`
**Policy**: `PATTERN_MANDATORY`
**Uncertainty Behavior**: `RUN` (when in doubt, run it)

### High Confidence Patterns (Any match → RUN)
```yaml
deadlock_patterns:
  high_confidence:
    - regex: 'lock\s*\([^)]+\)'                    # lock(obj)
    - regex: '\bMutex\b'                           # Mutex usage
    - regex: '\bSemaphore(Slim)?\b'                # Semaphore usage
    - regex: '\bMonitor\.(Enter|Exit|Wait|Pulse|TryEnter)'  # Monitor primitives
    - regex: '\bReaderWriterLock(Slim)?\b'         # RW locks
    - regex: '\bRwLock\b'                          # Rust RwLock
    - regex: '\bSpinLock|SpinWait\b'               # Spin primitives
    - regex: '\bManualResetEvent(Slim)?|AutoResetEvent\b'  # Event handles
    - regex: '\bCountdownEvent|Barrier\b'          # Synchronization barriers
    - file_contains: 'System.Threading.Tasks.Parallel'
    - file_contains: 'Interlocked.'
    - regex: '\bAtomic(Bool|I32|U32|I64|U64|Usize|Ptr)\b'  # Rust atomics
    - regex: '\bAtomicOrdering\b'                  # Rust memory ordering
    - regex: 'std::sync::'                         # Rust sync module
    - regex: 'std::thread::spawn|thread::spawn'    # Rust thread creation
    - regex: 'tokio::spawn|tokio::task'            # Tokio async tasks
    - regex: '\bpthread_create\b'                  # C/C++ thread creation
    - regex: 'new\s+Thread\('                      # .NET/Java thread creation
    - regex: '\bThreadPool\.(QueueUserWorkItem|UnsafeQueueUserWorkItem)\b'
    - regex: '\bpar_iter|par_bridge\b'             # Rayon parallel iterators
    - regex: '\brayon::\b'                         # Rayon crate
    - regex: '\bmpsc::|crossbeam::|flume::|async_channel::\b'  # Channel crates
    - regex: '\bChannel<|BlockingCollection\b'     # .NET channels
    - regex: '\bConcurrentQueue|ConcurrentBag|ConcurrentStack\b'  # Concurrent collections
    - regex: '\bArc<Mutex|Arc<RwLock\b'            # Rust shared-ownership locks
    - regex: '\bselect!|futures::select\b'         # Async select macros
    - regex: 'new\s+Worker\('                      # Web workers
```

### High Confidence Patterns — Removed Lines (Sync primitive removed → RUN)
```yaml
  high_confidence_removed:
    - regex: '\bMutex|RwLock\b'                    # Lock removed
    - regex: '\.lock\(\)'                          # Lock call removed
    - regex: '\bsynchronized\b'                    # Java sync removed
    - regex: '\bSemaphore\b'                       # Semaphore removed
    - regex: 'lock\s*\('                           # C# lock removed
    - regex: '\bMonitor\.(Enter|Exit)\b'           # Monitor removed
    - regex: '\bSpinLock\b'                        # SpinLock removed
    - regex: '\bAtomic(Bool|I32|U32|I64|U64|Usize)\b'  # Atomic removed
    - regex: '\bInterlocked\.\b'                   # Interlocked removed
    - regex: '\bArc<Mutex|Arc<RwLock\b'            # Shared lock removed
    - regex: '\bConcurrentDictionary\b'            # Concurrent collection removed
```

### Medium Confidence Patterns (2+ matches → RUN)
```yaml
  medium_confidence:
    - regex: '\basync\s+\w+\s+\w+\s*\('           # async method declaration
    - regex: '\bawait\b'                           # await keyword
    - regex: '\.Wait\(\)'                          # Blocking wait
    - regex: '\.Result\b'                          # Blocking result access
    - regex: '\bTask\.Run\b'                       # Task.Run usage
    - regex: '\bTask\.WhenAll\b'                   # Concurrent task wait
    - regex: '\bTask\.WhenAny\b'                   # Race condition potential
    - regex: '\bConcurrentDictionary\b'            # Concurrent collections
    - regex: '\bConcurrentQueue\b'
    - regex: '\bConcurrentBag\b'
```

### Trigger Rule
```
RUN if:
  - ANY high_confidence pattern matches, OR
  - 2+ medium_confidence patterns match, OR
  - Uncertainty about concurrency exists
```

---
