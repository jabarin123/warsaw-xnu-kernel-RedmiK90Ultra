#!/usr/bin/env python3
"""Apply warsaw OOM-eviction safety patches to AnnibaleGPU + MiniIOSurface."""
import pathlib
import sys

def replace_exact(path: pathlib.Path, old: str, new: str, label: str) -> None:
    txt = path.read_text("utf-8")
    n = txt.count(old)
    if n != 1:
        raise SystemExit(f"ERROR [{label}] in {path}: expected 1 match, found {n}")
    txt = txt.replace(old, new, 1)
    path.write_text(txt, encoding="utf-8", newline="\n")
    print(f"OK [{label}] -> {path}")

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {sys.argv[0]} <kernel-repo-root>")
    root = pathlib.Path(sys.argv[1])
    kdir = root / "xnu" / "files" / "iokit" / "Kernel"
    gpu_cpp = kdir / "AnnibaleGPU.cpp"
    gpu_mem = kdir / "AnnibaleGPUMemory.inc"
    surf_cpp = kdir / "MiniIOSurface.cpp"

    # 1. AnnibaleGPU.cpp: extern decls, client table, evictKept declaration, client registration in start()
    replace_exact(
        gpu_cpp,
        "void MiniIOMobileFramebufferShow(const uint8_t * pixels, uint32_t width, uint32_t height, uint32_t rowBytes);\n}",
        "void MiniIOMobileFramebufferShow(const uint8_t * pixels, uint32_t width, uint32_t height, uint32_t rowBytes);\n"
        "uint64_t MiniIOSurfaceEvictKept(void);\n"
        "uint64_t AnnibaleGPUEvictKept(void);\n"
        "}",
        "AnnibaleGPU externs",
    )

    replace_exact(
        gpu_cpp,
        "\tbool dropKept(struct gpu_kept * k, struct gpu_garbage * garbage = NULL);\n",
        "\tbool dropKept(struct gpu_kept * k, struct gpu_garbage * garbage = NULL);\n"
        "\tuint64_t drainKeptStep(struct gpu_garbage * garbage, bool * more);\n",
        "AnnibaleGPU drainKeptStep decl",
    )

    replace_exact(
        gpu_cpp,
        "OSDefineMetaClassAndStructors(AnnibaleGPUUserClient, IOUserClient);\n",
        "OSDefineMetaClassAndStructors(AnnibaleGPUUserClient, IOUserClient);\n\n"
        "#define GPU_CLIENTS_MAX 64\n"
        "static AnnibaleGPUUserClient * gGPUClients[GPU_CLIENTS_MAX];\n",
        "AnnibaleGPU gGPUClients",
    )

    replace_exact(
        gpu_cpp,
        "\tfKept = IONewZero(struct gpu_kept, GPU_KEPT);\n"
        "\tif (fKept == NULL) {\n"
        "\t\tIODelete(fBuffers, struct gpu_buffer, GPU_BUFFERS);\n"
        "\t\tfBuffers = NULL;\n"
        "\t\treturn false;\n"
        "\t}\n"
        "\treturn true;\n",
        "\tfKept = IONewZero(struct gpu_kept, GPU_KEPT);\n"
        "\tif (fKept == NULL) {\n"
        "\t\tIODelete(fBuffers, struct gpu_buffer, GPU_BUFFERS);\n"
        "\t\tfBuffers = NULL;\n"
        "\t\treturn false;\n"
        "\t}\n"
        "\tIOLockLock(gGPULock);\n"
        "\tfor (uint32_t i = 0; i < GPU_CLIENTS_MAX; i++) {\n"
        "\t\tif (gGPUClients[i] == NULL) {\n"
        "\t\t\tgGPUClients[i] = this;\n"
        "\t\t\tbreak;\n"
        "\t\t}\n"
        "\t}\n"
        "\tIOLockUnlock(gGPULock);\n"
        "\treturn true;\n",
        "AnnibaleGPU start register",
    )

    # 2. AnnibaleGPUMemory.inc: unregister on clientClose, drainKeptStep + AnnibaleGPUEvictKept, OOM retry in gemNew
    replace_exact(
        gpu_mem,
        "\t\tfor (uint32_t i = 0; i < GPU_KEPT; i++) {\n"
        "\t\t\tdropKept(&fKept[i], &garbage);\n"
        "\t\t}\n",
        "\t\tfor (uint32_t i = 0; i < GPU_CLIENTS_MAX; i++) {\n"
        "\t\t\tif (gGPUClients[i] == this) {\n"
        "\t\t\t\tgGPUClients[i] = NULL;\n"
        "\t\t\t}\n"
        "\t\t}\n"
        "\t\tfor (uint32_t i = 0; i < GPU_KEPT; i++) {\n"
        "\t\t\tdropKept(&fKept[i], &garbage);\n"
        "\t\t}\n",
        "AnnibaleGPU clientClose unregister",
    )

    replace_exact(
        gpu_mem,
        "\tgpuGarbageReleaseMemory(garbage, k->memory);\n"
        "\tk->memory = NULL;\n"
        "\tfKeptBytes -= k->size;\n"
        "\treturn true;\n"
        "}\n",
        "\tgpuGarbageReleaseMemory(garbage, k->memory);\n"
        "\tk->memory = NULL;\n"
        "\tfKeptBytes -= k->size;\n"
        "\treturn true;\n"
        "}\n\n"
        "/* With gGPULock held: drain kept entries up to garbage capacity so teardown happens lock-free. */\n"
        "uint64_t\n"
        "AnnibaleGPUUserClient::drainKeptStep(struct gpu_garbage * garbage, bool * more)\n"
        "{\n"
        "\tuint64_t freed = 0;\n"
        "\tif (fKept == NULL) {\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "\treap(garbage, GPU_GARBAGE_CAP / 2);\n"
        "\tfor (uint32_t i = 0; i < GPU_KEPT; i++) {\n"
        "\t\tif (fKept[i].memory == NULL) {\n"
        "\t\t\tcontinue;\n"
        "\t\t}\n"
        "\t\tif (garbage->memory_count >= GPU_GARBAGE_CAP) {\n"
        "\t\t\t*more = true;\n"
        "\t\t\tbreak;\n"
        "\t\t}\n"
        "\t\tuint64_t sz = fKept[i].size;\n"
        "\t\tif (dropKept(&fKept[i], garbage)) {\n"
        "\t\t\tfreed += sz;\n"
        "\t\t}\n"
        "\t}\n"
        "\treturn freed;\n"
        "}\n\n"
        "extern \"C\" uint64_t\n"
        "AnnibaleGPUEvictKept(void)\n"
        "{\n"
        "\tuint64_t total = 0;\n"
        "\tif (gGPULock == NULL) {\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "\tfor (uint32_t pass = 0; pass < 32; pass++) {\n"
        "\t\tstruct gpu_garbage garbage = {};\n"
        "\t\tbool more = false;\n"
        "\t\tIOLockLock(gGPULock);\n"
        "\t\tif (!gGPUReported && annibale_gpu_ready()) {\n"
        "\t\t\tfor (uint32_t i = 0; i < GPU_CLIENTS_MAX; i++) {\n"
        "\t\t\t\tif (gGPUClients[i] != NULL) {\n"
        "\t\t\t\t\ttotal += gGPUClients[i]->drainKeptStep(&garbage, &more);\n"
        "\t\t\t\t\tif (garbage.memory_count >= GPU_GARBAGE_CAP) {\n"
        "\t\t\t\t\t\tmore = true;\n"
        "\t\t\t\t\t\tbreak;\n"
        "\t\t\t\t\t}\n"
        "\t\t\t\t}\n"
        "\t\t\t}\n"
        "\t\t}\n"
        "\t\tIOLockUnlock(gGPULock);\n"
        "\t\tgpuGarbageFlush(&garbage);\n"
        "\t\tif (!more) {\n"
        "\t\t\tbreak;\n"
        "\t\t}\n"
        "\t}\n"
        "\treturn total;\n"
        "}\n",
        "AnnibaleGPU AnnibaleGPUEvictKept",
    )

    replace_exact(
        gpu_mem,
        "\tmemory = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task, kIODirectionInOut | kIOMemoryKernelUserShared |\n"
        "\t    (gGPUCached ? 0 : kIOMapWriteCombineCache), (vm_size_t) size, PAGE_SIZE);\n"
        "\tif (memory == NULL) {\n"
        "\t\tIOLog(\"AnnibaleGPU: no memory for a buffer of %llu bytes (%u buffers, %llu MB with this client)\\n\", size, fBufferCount,\n"
        "\t\t    fBufferBytes >> 20);\n"
        "\t\treturn kIOReturnNoMemory;\n"
        "\t}\n",
        "\tmemory = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task, kIODirectionInOut | kIOMemoryKernelUserShared |\n"
        "\t    (gGPUCached ? 0 : kIOMapWriteCombineCache), (vm_size_t) size, PAGE_SIZE);\n"
        "\tif (memory == NULL) {\n"
        "\t\tuint64_t freed = AnnibaleGPUEvictKept() + MiniIOSurfaceEvictKept();\n"
        "\t\tif (freed != 0) {\n"
        "\t\t\tIOLog(\"AnnibaleGPU: reclaimed %llu MB from kept pools on OOM, retrying %llu bytes\\n\", freed >> 20, size);\n"
        "\t\t\tmemory = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task, kIODirectionInOut | kIOMemoryKernelUserShared |\n"
        "\t\t\t    (gGPUCached ? 0 : kIOMapWriteCombineCache), (vm_size_t) size, PAGE_SIZE);\n"
        "\t\t}\n"
        "\t}\n"
        "\tif (memory == NULL) {\n"
        "\t\tIOLog(\"AnnibaleGPU: no memory for a buffer of %llu bytes (%u buffers, %llu MB with this client)\\n\", size, fBufferCount,\n"
        "\t\t    fBufferBytes >> 20);\n"
        "\t\treturn kIOReturnNoMemory;\n"
        "\t}\n",
        "AnnibaleGPU gemNew OOM retry",
    )

    # 3. MiniIOSurface.cpp: MiniIOSurfaceEvictKept + OOM retry in surfaceMemory()
    replace_exact(
        surf_cpp,
        "extern \"C\" void FlushPoC_DcacheRegion(vm_offset_t va, size_t length);\n",
        "extern \"C\" void FlushPoC_DcacheRegion(vm_offset_t va, size_t length);\n"
        "extern \"C\" uint64_t AnnibaleGPUEvictKept(void);\n"
        "extern \"C\" uint64_t MiniIOSurfaceEvictKept(void);\n\n"
        "extern \"C\" uint64_t\n"
        "MiniIOSurfaceEvictKept(void)\n"
        "{\n"
        "\tIOBufferMemoryDescriptor * drop[SURFACE_KEPT] = {};\n"
        "\tuint64_t freed = 0;\n"
        "\tunsigned n = 0;\n"
        "\tif (gSurfaceKeptLock == NULL) {\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "\tIOLockLock(gSurfaceKeptLock);\n"
        "\tfor (unsigned i = 0; i < SURFACE_KEPT; i++) {\n"
        "\t\tif (gSurfaceKept[i].memory != NULL && !gSurfaceKept[i].busy) {\n"
        "\t\t\tdrop[n++] = gSurfaceKept[i].memory;\n"
        "\t\t\tfreed += gSurfaceKept[i].memory->getCapacity();\n"
        "\t\t\tgSurfaceKept[i].memory = NULL;\n"
        "\t\t\tgSurfaceKept[i].clear = false;\n"
        "\t\t}\n"
        "\t}\n"
        "\tIOLockUnlock(gSurfaceKeptLock);\n"
        "\tfor (unsigned i = 0; i < n; i++) {\n"
        "\t\tdrop[i]->release();\n"
        "\t}\n"
        "\treturn freed;\n"
        "}\n",
        "MiniIOSurface MiniIOSurfaceEvictKept",
    )

    replace_exact(
        surf_cpp,
        "\tif (memory == NULL) {\n"
        "\t\tmemory = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task, kIODirectionInOut | kIOMemoryKernelUserShared, bytes,\n"
        "\t\t    PAGE_SIZE);\n"
        "\t\tif (memory != NULL) {\n"
        "\t\t\tFlushPoC_DcacheRegion((vm_offset_t) memory->getBytesNoCopy(), bytes);\n"
        "\t\t}\n"
        "\t}\n",
        "\tif (memory == NULL) {\n"
        "\t\tmemory = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task, kIODirectionInOut | kIOMemoryKernelUserShared, bytes,\n"
        "\t\t    PAGE_SIZE);\n"
        "\t\tif (memory == NULL) {\n"
        "\t\t\tuint64_t freed = AnnibaleGPUEvictKept() + MiniIOSurfaceEvictKept();\n"
        "\t\t\tif (freed != 0) {\n"
        "\t\t\t\tIOLog(\"MiniIOSurface: reclaimed %llu MB from kept pools on OOM, retrying %llu bytes\\n\", freed >> 20, (unsigned long long) bytes);\n"
        "\t\t\t\tmemory = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task, kIODirectionInOut | kIOMemoryKernelUserShared, bytes,\n"
        "\t\t\t\t    PAGE_SIZE);\n"
        "\t\t\t}\n"
        "\t\t}\n"
        "\t\tif (memory != NULL) {\n"
        "\t\t\tFlushPoC_DcacheRegion((vm_offset_t) memory->getBytesNoCopy(), bytes);\n"
        "\t\t}\n"
        "\t}\n",
        "MiniIOSurface surfaceMemory OOM retry",
    )

    print("ALL warsaw GPU/Surface OOM patches applied cleanly.")

if __name__ == "__main__":
    main()
