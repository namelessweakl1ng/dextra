# Benchmarks

This directory holds Dextra programs used to measure compiler and runtime
performance over time.  Run a benchmark manually:

```bash
dextra build benchmarks/fibonacci.dx -o /tmp/fib
time /tmp/fib
```

The purpose of these benchmarks is *not* to claim that Dextra beats C++ or
Rust.  The purpose is to measure compiler and runtime behavior over time, so
that changes to the compiler can be evaluated quantitatively.

Tracked metrics:

- compile time (cold)
- compile time (warm, with `llvmlite` already imported)
- binary size
- runtime
- peak memory

Planned benchmarks:

- `fibonacci.dx` — recursion-heavy
- `factorial.dx` — tail-call-friendly
- `prime_sieve.dx` — array + loop
- `matrix_mul.dx` — nested arrays
- `string_concat.dx` — string operations
