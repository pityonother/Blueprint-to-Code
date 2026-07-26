// Export selected native functions, stable native:// evidence IDs, and decompiler output.
//@category BlueprintToCode

import java.io.File;
import java.io.FileWriter;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.app.util.bin.format.pdb.PdbParserConstants;
import ghidra.framework.options.Options;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Program;

public class ExportNativeTargets extends GhidraScript {

	private static final int DECOMPILE_TIMEOUT_SECONDS = 120;
	private static final List<String> DEFAULT_PATTERNS = Arrays.asList(
		"GenerateCrateItems",
		"GenerateCustomCrateItems",
		"ClampItemRating",
		"GetItemQualityIndex",
		"OverrideItemRating"
	);

	@Override
	protected void run() throws Exception {
		String[] args = getScriptArgs();
		if (args.length < 1) {
			throw new IllegalArgumentException(
				"ExportNativeTargets.java expects an output JSON path followed by optional name patterns");
		}

		File outputFile = new File(args[0]).getAbsoluteFile();
		File parent = outputFile.getParentFile();
		if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
			throw new IllegalStateException("Could not create evidence directory: " + parent);
		}

		List<String> patterns = new ArrayList<>();
		if (args.length > 1) {
			patterns.addAll(Arrays.asList(args).subList(1, args.length));
		}
		else {
			patterns.addAll(DEFAULT_PATTERNS);
		}

		List<Function> matches = findMatches(patterns);
		boolean pdbLoaded = isPdbLoaded();
		String binarySha = currentProgram.getExecutableSHA256();
		if (binarySha == null || binarySha.isBlank()) {
			binarySha = "unknown-sha256";
		}
		binarySha = binarySha.toLowerCase(Locale.ROOT);

		JsonObject root = new JsonObject();
		root.addProperty("schema", "blueprint-to-code-native-targets/v1");
		root.addProperty("generatedAtUtc", Instant.now().toString());
		root.addProperty("program", currentProgram.getName());
		root.addProperty("executablePath", currentProgram.getExecutablePath());
		root.addProperty("binarySha256", binarySha);
		root.addProperty("imageBase", currentProgram.getImageBase().toString());
		root.addProperty("languageId", currentProgram.getLanguageID().toString());
		root.addProperty("compilerSpecId", currentProgram.getCompilerSpec().getCompilerSpecID().toString());
		root.addProperty("pdbLoaded", pdbLoaded);
		root.addProperty("decompileTimeoutSeconds", DECOMPILE_TIMEOUT_SECONDS);

		JsonArray requestedPatterns = new JsonArray();
		for (String pattern : patterns) {
			requestedPatterns.add(pattern);
		}
		root.add("patterns", requestedPatterns);

		JsonArray functions = new JsonArray();
		DecompInterface decompiler = createDecompiler();
		try {
			for (Function function : matches) {
				if (monitor.isCancelled()) {
					break;
				}
				functions.add(exportFunction(function, decompiler, binarySha, pdbLoaded));
			}
		}
		finally {
			decompiler.dispose();
		}

		root.addProperty("matchCount", functions.size());
		root.add("functions", functions);

		Gson gson = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
		try (FileWriter writer = new FileWriter(outputFile)) {
			gson.toJson(root, writer);
		}

		println("Exported " + functions.size() + " native target functions to " + outputFile);
	}

	private List<Function> findMatches(List<String> patterns) {
		List<Function> matches = new ArrayList<>();
		FunctionIterator iterator = currentProgram.getFunctionManager().getFunctions(true);
		while (iterator.hasNext() && !monitor.isCancelled()) {
			Function function = iterator.next();
			String simpleName = function.getName().toLowerCase(Locale.ROOT);
			for (String pattern : patterns) {
				if (simpleName.equals(pattern.toLowerCase(Locale.ROOT))) {
					matches.add(function);
					break;
				}
			}
		}
		matches.sort(Comparator.comparing(function -> function.getName(true)));
		return matches;
	}

	private DecompInterface createDecompiler() {
		DecompInterface decompiler = new DecompInterface();
		DecompileOptions options = new DecompileOptions();
		decompiler.setOptions(options);
		decompiler.setSimplificationStyle("decompile");
		if (!decompiler.openProgram(currentProgram)) {
			throw new IllegalStateException("Decompiler could not open " + currentProgram.getName());
		}
		return decompiler;
	}

	private JsonObject exportFunction(
			Function function,
			DecompInterface decompiler,
			String binarySha,
			boolean pdbLoaded) {
		Address entry = function.getEntryPoint();
		long rva = entry.subtract(currentProgram.getImageBase());
		String rvaText = String.format("0x%X", rva);

		JsonObject json = new JsonObject();
		json.addProperty(
			"evidenceId",
			"native://" + binarySha + "/" + currentProgram.getName() + "/" + rvaText);
		json.addProperty("name", function.getName());
		json.addProperty("qualifiedName", function.getName(true));
		json.addProperty("entryPoint", entry.toString());
		json.addProperty("rva", rvaText);
		json.addProperty("signature", function.getPrototypeString(true, true));
		json.addProperty("symbolSource", function.getSymbol().getSource().toString());
		json.addProperty("confidence", pdbLoaded ? "pdb-symbol-plus-decompiler" : "binary-analysis");

		DecompileResults results =
			decompiler.decompileFunction(function, DECOMPILE_TIMEOUT_SECONDS, monitor);
		boolean completed = results != null && results.decompileCompleted();
		json.addProperty("decompileCompleted", completed);
		if (completed && results.getDecompiledFunction() != null) {
			json.addProperty("decompiledC", results.getDecompiledFunction().getC());
		}
		else {
			String error = results == null ? "No decompiler result" : results.getErrorMessage();
			json.addProperty("decompileError", error);
		}
		return json;
	}

	private boolean isPdbLoaded() {
		Options options = currentProgram.getOptions(Program.PROGRAM_INFO);
		return options.getBoolean(PdbParserConstants.PDB_LOADED, false);
	}
}
