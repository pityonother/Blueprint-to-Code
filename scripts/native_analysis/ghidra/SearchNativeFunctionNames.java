// Search PDB-backed native function names without decompiling every match.
//@category BlueprintToCode

import java.io.File;
import java.io.FileWriter;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import ghidra.app.script.GhidraScript;
import ghidra.app.util.bin.format.pdb.PdbParserConstants;
import ghidra.framework.options.Options;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Program;

public class SearchNativeFunctionNames extends GhidraScript {

	private static final int MAX_MATCHES = 2000;

	@Override
	protected void run() throws Exception {
		String[] args = getScriptArgs();
		if (args.length < 2) {
			throw new IllegalArgumentException(
				"SearchNativeFunctionNames.java expects an output JSON path and one or more substrings");
		}

		File outputFile = new File(args[0]).getAbsoluteFile();
		File parent = outputFile.getParentFile();
		if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
			throw new IllegalStateException("Could not create output directory: " + parent);
		}

		List<String> patterns = new ArrayList<>();
		for (int index = 1; index < args.length; index++) {
			patterns.add(args[index].toLowerCase(Locale.ROOT));
		}

		List<Function> matches = new ArrayList<>();
		FunctionIterator iterator = currentProgram.getFunctionManager().getFunctions(true);
		while (iterator.hasNext() && !monitor.isCancelled()) {
			Function function = iterator.next();
			String qualifiedName = function.getName(true).toLowerCase(Locale.ROOT);
			for (String pattern : patterns) {
				if (qualifiedName.contains(pattern)) {
					matches.add(function);
					break;
				}
			}
			if (matches.size() >= MAX_MATCHES) {
				break;
			}
		}
		matches.sort(Comparator.comparing(function -> function.getName(true)));

		String binarySha = currentProgram.getExecutableSHA256();
		if (binarySha == null || binarySha.isBlank()) {
			binarySha = "unknown-sha256";
		}
		binarySha = binarySha.toLowerCase(Locale.ROOT);

		JsonObject root = new JsonObject();
		root.addProperty("schema", "blueprint-to-code-native-name-search/v1");
		root.addProperty("generatedAtUtc", Instant.now().toString());
		root.addProperty("program", currentProgram.getName());
		root.addProperty("binarySha256", binarySha);
		root.addProperty("imageBase", currentProgram.getImageBase().toString());
		root.addProperty("pdbLoaded", isPdbLoaded());
		root.addProperty("maxMatches", MAX_MATCHES);
		root.addProperty("truncated", matches.size() >= MAX_MATCHES);

		JsonArray requestedPatterns = new JsonArray();
		for (String pattern : patterns) {
			requestedPatterns.add(pattern);
		}
		root.add("patterns", requestedPatterns);

		JsonArray functions = new JsonArray();
		for (Function function : matches) {
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
			functions.add(json);
		}
		root.addProperty("matchCount", functions.size());
		root.add("functions", functions);

		Gson gson = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
		try (FileWriter writer = new FileWriter(outputFile)) {
			gson.toJson(root, writer);
		}

		println("Found " + functions.size() + " matching native functions in " + outputFile);
	}

	private boolean isPdbLoaded() {
		Options options = currentProgram.getOptions(Program.PROGRAM_INFO);
		return options.getBoolean(PdbParserConstants.PDB_LOADED, false);
	}
}
