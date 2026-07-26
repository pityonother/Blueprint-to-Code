// Find PDB-backed native callers for selected exact function names.
//@category BlueprintToCode

import java.io.File;
import java.io.FileWriter;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

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
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

public class FindNativeCallers extends GhidraScript {

	@Override
	protected void run() throws Exception {
		String[] args = getScriptArgs();
		if (args.length < 2) {
			throw new IllegalArgumentException(
				"FindNativeCallers.java expects an output JSON path and one or more exact function names");
		}

		File outputFile = new File(args[0]).getAbsoluteFile();
		File parent = outputFile.getParentFile();
		if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
			throw new IllegalStateException("Could not create output directory: " + parent);
		}

		List<String> requestedNames = new ArrayList<>();
		for (int index = 1; index < args.length; index++) {
			requestedNames.add(args[index]);
		}

		String binarySha = currentProgram.getExecutableSHA256();
		if (binarySha == null || binarySha.isBlank()) {
			binarySha = "unknown-sha256";
		}
		binarySha = binarySha.toLowerCase(Locale.ROOT);

		JsonObject root = new JsonObject();
		root.addProperty("schema", "blueprint-to-code-native-callers/v1");
		root.addProperty("generatedAtUtc", Instant.now().toString());
		root.addProperty("program", currentProgram.getName());
		root.addProperty("binarySha256", binarySha);
		root.addProperty("imageBase", currentProgram.getImageBase().toString());
		root.addProperty("pdbLoaded", isPdbLoaded());

		JsonArray targets = new JsonArray();
		for (String requestedName : requestedNames) {
			if (monitor.isCancelled()) {
				break;
			}
			targets.add(exportTarget(requestedName, binarySha));
		}
		root.add("targets", targets);

		Gson gson = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
		try (FileWriter writer = new FileWriter(outputFile)) {
			gson.toJson(root, writer);
		}

		println("Exported callers for " + targets.size() + " native targets to " + outputFile);
	}

	private JsonObject exportTarget(String requestedName, String binarySha) {
		JsonObject targetJson = new JsonObject();
		targetJson.addProperty("requestedName", requestedName);

		List<Function> matches = findExactMatches(requestedName);
		targetJson.addProperty("matchCount", matches.size());
		JsonArray matchArray = new JsonArray();
		for (Function target : matches) {
			matchArray.add(exportFunctionAndCallers(target, binarySha));
		}
		targetJson.add("matches", matchArray);
		return targetJson;
	}

	private List<Function> findExactMatches(String requestedName) {
		String needle = requestedName.toLowerCase(Locale.ROOT);
		List<Function> matches = new ArrayList<>();
		FunctionIterator iterator = currentProgram.getFunctionManager().getFunctions(true);
		while (iterator.hasNext() && !monitor.isCancelled()) {
			Function function = iterator.next();
			if (function.getName().toLowerCase(Locale.ROOT).equals(needle) ||
					function.getName(true).toLowerCase(Locale.ROOT).equals(needle)) {
				matches.add(function);
			}
		}
		matches.sort(Comparator.comparing(function -> function.getName(true)));
		return matches;
	}

	private JsonObject exportFunctionAndCallers(Function target, String binarySha) {
		JsonObject json = functionIdentity(target, binarySha);
		Map<String, JsonObject> callersByEntryPoint = new LinkedHashMap<>();
		JsonArray incomingReferences = new JsonArray();

		ReferenceIterator iterator =
			currentProgram.getReferenceManager().getReferencesTo(target.getEntryPoint());
		while (iterator.hasNext() && !monitor.isCancelled()) {
			Reference reference = iterator.next();
			JsonObject referenceJson = new JsonObject();
			referenceJson.addProperty("fromAddress", reference.getFromAddress().toString());
			referenceJson.addProperty("referenceType", reference.getReferenceType().getName());
			referenceJson.addProperty("isCall", reference.getReferenceType().isCall());

			Function caller =
				currentProgram.getFunctionManager().getFunctionContaining(reference.getFromAddress());
			if (caller != null) {
				referenceJson.addProperty("callerQualifiedName", caller.getName(true));
				referenceJson.addProperty("callerEntryPoint", caller.getEntryPoint().toString());
				callersByEntryPoint.putIfAbsent(
					caller.getEntryPoint().toString(),
					functionIdentity(caller, binarySha));
			}
			incomingReferences.add(referenceJson);
		}

		List<JsonObject> sortedCallers = new ArrayList<>(callersByEntryPoint.values());
		sortedCallers.sort(Comparator.comparing(
			caller -> caller.get("qualifiedName").getAsString()));
		JsonArray callers = new JsonArray();
		for (JsonObject caller : sortedCallers) {
			callers.add(caller);
		}
		json.addProperty("incomingReferenceCount", incomingReferences.size());
		json.addProperty("callerCount", callers.size());
		json.add("callers", callers);
		json.add("incomingReferences", incomingReferences);
		return json;
	}

	private JsonObject functionIdentity(Function function, String binarySha) {
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
		return json;
	}

	private boolean isPdbLoaded() {
		Options options = currentProgram.getOptions(Program.PROGRAM_INFO);
		return options.getBoolean(PdbParserConstants.PDB_LOADED, false);
	}
}
