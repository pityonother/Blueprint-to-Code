# Public native pipeline fixture

This fixture is original test code and contains no ARK, Unreal, Ghidra, or other
proprietary binary data. It deliberately exposes:

- two `ComputeQuality` overloads;
- a virtual method and vtable;
- `QualityInputs` field reads and writes;
- `QualityEntry → QualityMiddle → QualityLeaf → ComputeQuality`;
- integer and floating-point constants;
- an explicit branch and return-value calculation.

Build it on Windows with Visual Studio 2022 C++ x64 tools:

```powershell
.\tests\native_fixture\build.ps1
```

Generated DLL, PDB, object files, and manifests stay under
`tests/native_fixture/build/` and are ignored. The fixture recipe is:

```text
scripts/native_analysis/recipes/test-native-fixture.v1.json
```

The fixture proves selection, provenance, export, indexing, and bounded query
behavior. It does not prove that an ARK build has the same functions or runtime
behavior.
