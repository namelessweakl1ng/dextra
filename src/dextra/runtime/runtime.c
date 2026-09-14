/* Dextra runtime — minimal C support library.
 *
 * Provides:
 *   - DxString: length-prefixed UTF-8 string struct.
 *   - dx_string_new, dx_string_concat, dx_string_print, dx_string_println, dx_string_length, dx_string_eq.
 *   - DxArray: type-erased length-prefixed array of i64 slots (used for Int and pointer types).
 *   - dx_alloc_struct: heap allocator for struct literals (returns stable pointers safe to return from functions).
 *   - print/println overloads for Int, Float, Bool, String.
 *
 * Memory is malloc'd and never freed in v0.1.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* ----------------- DxString ----------------- */

typedef struct {
    int64_t length;
    char *data;
} DxString;

DxString *dx_string_new(const char *bytes, int64_t len) {
    DxString *s = (DxString *)malloc(sizeof(DxString));
    s->length = len;
    s->data = (char *)malloc((size_t)len + 1);
    if (len > 0) memcpy(s->data, bytes, (size_t)len);
    s->data[len] = '\0';
    return s;
}

DxString *dx_string_concat(DxString *a, DxString *b) {
    int64_t new_len = a->length + b->length;
    DxString *s = (DxString *)malloc(sizeof(DxString));
    s->length = new_len;
    s->data = (char *)malloc((size_t)new_len + 1);
    if (a->length > 0) memcpy(s->data, a->data, (size_t)a->length);
    if (b->length > 0) memcpy(s->data + a->length, b->data, (size_t)b->length);
    s->data[new_len] = '\0';
    return s;
}

int64_t dx_string_length(DxString *s) {
    return s->length;
}

int64_t dx_string_eq(DxString *a, DxString *b) {
    if (a == b) return 1;
    if (a->length != b->length) return 0;
    if (a->length == 0) return 1;
    return memcmp(a->data, b->data, (size_t)a->length) == 0 ? 1 : 0;
}

void dx_string_print(DxString *s) {
    fwrite(s->data, 1, (size_t)s->length, stdout);
}

void dx_string_println(DxString *s) {
    fwrite(s->data, 1, (size_t)s->length, stdout);
    fputc('\n', stdout);
}

/* ----------------- DxArray ----------------- */
/* Element type is i64-sized; we store Int directly and pointers for
 * structs/strings (which are themselves heap-allocated).
 */

typedef struct {
    int64_t length;
    int64_t *data;
} DxArray;

DxArray *dx_array_new(int64_t length) {
    DxArray *a = (DxArray *)malloc(sizeof(DxArray));
    a->length = length;
    a->data = (int64_t *)calloc((size_t)(length > 0 ? length : 1), sizeof(int64_t));
    return a;
}

int64_t dx_array_length(DxArray *a) {
    return a->length;
}

int64_t dx_array_get(DxArray *a, int64_t i) {
    return a->data[i];
}

void dx_array_set(DxArray *a, int64_t i, int64_t v) {
    a->data[i] = v;
}

/* ----------------- print overloads ----------------- */

void dx_print_int(int64_t x)     { printf("%lld", (long long)x); }
void dx_print_float(double x)    { printf("%g", x); }
void dx_print_bool(int64_t x)    { fputs(x ? "true" : "false", stdout); }
void dx_println_int(int64_t x)   { printf("%lld\n", (long long)x); }
void dx_println_float(double x)  { printf("%g\n", x); }
void dx_println_bool(int64_t x)  { puts(x ? "true" : "false"); }
void dx_println_newline(void)    { fputc('\n', stdout); }

void dx_print_string(DxString *s) { dx_string_print(s); }
void dx_println_string(DxString *s) { dx_string_println(s); }

/* ----------------- Struct heap allocation ----------------- */
/*
 * Struct literals are heap-allocated so that returning a struct from a
 * function returns a stable pointer rather than a dangling stack address.
 * The size is passed in bytes by the caller; the allocator returns a
 * zero-initialized block of that size.  Memory is never freed in v0.1.
 */

void *dx_alloc_struct(int64_t size) {
    void *p = malloc((size_t)size);
    if (p == NULL) {
        fputs("dextra: out of memory in dx_alloc_struct\n", stderr);
        exit(70);
    }
    /* Zero-initialize so unset fields are deterministic. */
    memset(p, 0, (size_t)size);
    return p;
}
