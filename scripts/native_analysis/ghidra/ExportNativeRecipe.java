// Export native evidence selected by one versioned, path-free analysis recipe.
//@category BlueprintToCode

import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.HashMap;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Queue;
import java.util.Set;
import java.util.regex.Pattern;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.app.util.bin.format.pdb.PdbParserConstants;
import ghidra.framework.options.Options;
import ghidra.program.model.address.Address;
import ghidra.program.model.data.DataType;
import ghidra.program.model.data.DataTypeComponent;
import ghidra.program.model.data.Structure;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.listing.Parameter;
import ghidra.program.model.listing.Program;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolIterator;

public class ExportNativeRecipe extends GhidraScript {

	private static final int DECOMPILE_TIMEOUT_SECONDS = 120;
	private JsonObject recipe;
	private JsonObject budgets;
	private String binarySha;
	private boolean pdbLoaded;
	private int callEdgeCount;
	private int fieldAccessCount;
	private int constantCount;
	private int totalDecompiledCharacters;
	private boolean constantBudgetGapRecorded;
	private final JsonArray gaps = new JsonArray();
	private final Map<String, Function> functionsByEvidenceId = new LinkedHashMap<>();
	private final Map<String, JsonObject> exportedFunctions = new LinkedHashMap<>();
	private final Map<String, JsonObject> exportsByEvidenceId = new LinkedHashMap<>();
	private final Map<String, List<Function>> targetMatches = new LinkedHashMap<>();
	private final Map<String, JsonArray> vtableSlotsByEvidenceId = new LinkedHashMap<>();

	@Override
	protected void run() throws Exception {
		String[] args = getScriptArgs();
		if (args.length != 5) {
			throw new IllegalArgumentException(
				"ExportNativeRecipe.java expects output JSON, recipe JSON, recipe SHA-256, formal|experimental, and analysis timeout seconds");
		}
		File outputFile = new File(args[0]).getAbsoluteFile();
		File recipeFile = new File(args[1]).getAbsoluteFile();
		String recipeSha = args[2].toLowerCase(Locale.ROOT);
		boolean formal = "formal".equalsIgnoreCase(args[3]);
		int analysisTimeoutSeconds = Integer.parseInt(args[4]);
		if (!formal && !"experimental".equalsIgnoreCase(args[3])) {
			throw new IllegalArgumentException("Recipe mode must be formal or experimental");
		}
		File parent = outputFile.getParentFile();
		if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
			throw new IllegalStateException("Could not create evidence directory: " + parent);
		}
		try (FileReader reader = new FileReader(recipeFile, StandardCharsets.UTF_8)) {
			recipe = JsonParser.parseReader(reader).getAsJsonObject();
		}
		budgets = recipe.getAsJsonObject("budgets");
		binarySha = currentProgram.getExecutableSHA256();
		if (binarySha == null || binarySha.isBlank()) {
			binarySha = "unknown-sha256";
		}
		binarySha = binarySha.toLowerCase(Locale.ROOT);
		pdbLoaded = isPdbLoaded();

		List<Function> allFunctions = allFunctions();
		JsonArray targetResults = resolveTargets(allFunctions, formal);
		collectTraversalFunctions();
		JsonArray vtableResults = resolveVtableQueries();
		exportCollectedFunctions();
		JsonArray fieldResults = resolveFieldQueries();

		JsonObject root = new JsonObject();
		root.addProperty("schema", "blueprint-to-code-native-recipe-export/v1");
		root.addProperty("generatedAtUtc", Instant.now().toString());
		root.addProperty("program", currentProgram.getName());
		root.addProperty("binarySha256", binarySha);
		root.addProperty("imageBase", currentProgram.getImageBase().toString());
		root.addProperty("languageId", currentProgram.getLanguageID().toString());
		root.addProperty(
			"compilerSpecId",
			currentProgram.getCompilerSpec().getCompilerSpecID().toString());
		root.addProperty("pdbLoaded", pdbLoaded);
		Options programOptions = currentProgram.getOptions(Program.PROGRAM_INFO);
		root.addProperty("pdbFile", programOptions.getString(PdbParserConstants.PDB_FILE, ""));
		root.addProperty("pdbGuid", programOptions.getString(PdbParserConstants.PDB_GUID, ""));
		root.addProperty("pdbAge", programOptions.getString(PdbParserConstants.PDB_AGE, ""));
		root.addProperty("pdbSignature", programOptions.getString(PdbParserConstants.PDB_SIGNATURE, ""));
		root.addProperty("pdbVersion", programOptions.getString(PdbParserConstants.PDB_VERSION, ""));
		root.addProperty("recipeId", recipe.get("recipeId").getAsString());
		root.addProperty("recipeSha256", recipeSha);
		root.addProperty("mode", formal ? "formal" : "experimental");
		root.addProperty("analysisTimeoutSeconds", analysisTimeoutSeconds);
		root.addProperty("decompileTimeoutSeconds", DECOMPILE_TIMEOUT_SECONDS);
		root.add("targetResults", targetResults);
		root.add("fieldQueryResults", fieldResults);
		root.add("vtableQueryResults", vtableResults);
		JsonArray functions = new JsonArray();
		for (JsonObject function : exportedFunctions.values()) {
			functions.add(function);
		}
		root.addProperty("matchCount", functions.size());
		root.add("functions", functions);
		root.add("gaps", gaps);
		JsonObject usage = new JsonObject();
		usage.addProperty("functions", functions.size());
		usage.addProperty("callEdges", callEdgeCount);
		usage.addProperty("fieldAccesses", fieldAccessCount);
		usage.addProperty("constants", constantCount);
		usage.addProperty("decompiledCharacters", totalDecompiledCharacters);
		root.add("budgetUsage", usage);

		Gson gson = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
		try (FileWriter writer = new FileWriter(outputFile, StandardCharsets.UTF_8)) {
			gson.toJson(root, writer);
		}
		println(
			"Exported " + targetResults.size() + " recipe targets and " +
			functions.size() + " native functions to " + outputFile);
	}

	private List<Function> allFunctions() {
		List<Function> functions = new ArrayList<>();
		FunctionIterator iterator = currentProgram.getFunctionManager().getFunctions(true);
		while (iterator.hasNext() && !monitor.isCancelled()) {
			Function function = iterator.next();
			if (isExportableFunction(function)) {
				functions.add(function);
			}
		}
		functions.sort(Comparator
			.comparing((Function function) -> function.getName(true))
			.thenComparing(function -> function.getEntryPoint().toString()));
		return functions;
	}

	private JsonArray resolveTargets(List<Function> allFunctions, boolean formal) {
		JsonArray results = new JsonArray();
		for (JsonElement targetElement : recipe.getAsJsonArray("targets")) {
			JsonObject target = targetElement.getAsJsonObject();
			JsonObject selector = target.getAsJsonObject("selector");
			String targetId = target.get("id").getAsString();
			List<Function> candidates = candidatePool(allFunctions, selector, formal);
			List<Function> accepted = new ArrayList<>();
			JsonArray candidateJson = new JsonArray();
			for (Function candidate : candidates) {
				String rejection = rejectionReason(candidate, selector, formal);
				boolean isAccepted = rejection.isEmpty();
				JsonObject row = functionIdentity(candidate);
				row.addProperty("accepted", isAccepted);
				row.addProperty("rejectionReason", rejection);
				candidateJson.add(row);
				if (isAccepted) {
					accepted.add(candidate);
					addFunction(
						candidate,
						"recipe target " + targetId,
						target.getAsJsonObject("exports"));
				}
			}
			accepted.sort(Comparator.comparing(function -> function.getEntryPoint().toString()));
			targetMatches.put(targetId, accepted);
			JsonObject result = new JsonObject();
			result.addProperty("targetId", targetId);
			result.add("selector", selector.deepCopy());
			result.add("exports", target.getAsJsonObject("exports").deepCopy());
			result.addProperty("expectedMatches", target.get("expectedMatches").getAsInt());
			result.addProperty("matchCount", accepted.size());
			result.add("resolvedEvidenceIds", evidenceIds(accepted));
			result.add("candidates", candidateJson);
			result.addProperty(
				"status",
				accepted.size() == target.get("expectedMatches").getAsInt()
					? "CONFIRMED" : "COUNT_MISMATCH");
			results.add(result);
		}
		return results;
	}

	private List<Function> candidatePool(
			List<Function> allFunctions,
			JsonObject selector,
			boolean formal) {
		List<Function> candidates = new ArrayList<>();
		if (selector.has("rva")) {
			long rva = Long.decode(selector.get("rva").getAsString());
			Address address = currentProgram.getImageBase().add(rva);
			Function function = currentProgram.getFunctionManager().getFunctionAt(address);
			if (isExportableFunction(function)) {
				candidates.add(function);
			}
			return candidates;
		}
		if (selector.has("qualifiedName")) {
			String qualifiedName = selector.get("qualifiedName").getAsString();
			String simpleName = simplePart(qualifiedName);
			for (Function function : allFunctions) {
				if (canonicalQualifiedName(function.getName(true))
						.equals(canonicalQualifiedName(qualifiedName)) ||
						function.getName().equals(simpleName)) {
					candidates.add(function);
				}
			}
			return candidates;
		}
		if (selector.has("simpleName")) {
			String simpleName = selector.get("simpleName").getAsString();
			for (Function function : allFunctions) {
				if (function.getName().equals(simpleName)) {
					candidates.add(function);
				}
			}
			return candidates;
		}
		if (selector.has("regex") && !formal) {
			Pattern pattern = Pattern.compile(selector.get("regex").getAsString());
			for (Function function : allFunctions) {
				if (pattern.matcher(function.getName(true)).find()) {
					candidates.add(function);
				}
			}
		}
		return candidates;
	}

	private String rejectionReason(Function function, JsonObject selector, boolean formal) {
		if (selector.has("rva")) {
			return formatRva(function).equalsIgnoreCase(selector.get("rva").getAsString())
				? "" : "RVA differs from the explicit selector.";
		}
		if (selector.has("qualifiedName") &&
				!canonicalQualifiedName(function.getName(true)).equals(
					canonicalQualifiedName(
						selector.get("qualifiedName").getAsString()))) {
			return "Qualified name differs from the explicit selector.";
		}
		if (selector.has("simpleName")) {
			if (!selector.has("allowSimpleName") ||
					!selector.get("allowSimpleName").getAsBoolean()) {
				return "Simple-name selection was not explicitly enabled.";
			}
			if (!function.getName().equals(selector.get("simpleName").getAsString())) {
				return "Simple name differs from the explicit selector.";
			}
		}
		if (selector.has("regex")) {
			if (formal) {
				return "Regex selectors are forbidden in formal mode.";
			}
			if (!Pattern.compile(selector.get("regex").getAsString())
					.matcher(function.getName(true)).find()) {
				return "Qualified name does not match the discovery regex.";
			}
		}
		if (selector.has("signature")) {
			String requested = normalizeSignature(selector.get("signature").getAsString());
			String actual = normalizeSignature(canonicalSignature(function));
			if (!actual.equals(requested)) {
				return "Canonical signature differs: " + canonicalSignature(function);
			}
		}
		return "";
	}

	private String canonicalSignature(Function function) {
		StringBuilder builder = new StringBuilder();
		builder.append(function.getReturnType().getDisplayName());
		builder.append(" ");
		builder.append(function.getName(true));
		builder.append("(");
		Parameter[] parameters = function.getParameters();
		for (int index = 0; index < parameters.length; index++) {
			if (index > 0) {
				builder.append(",");
			}
			builder.append(parameters[index].getDataType().getDisplayName());
		}
		builder.append(")");
		return builder.toString();
	}

	private String normalizeSignature(String value) {
		return canonicalQualifiedName(value)
			.replaceAll("\\s+", "")
			.replace("__cdecl", "");
	}

	private String simplePart(String qualifiedName) {
		int separator = qualifiedName.lastIndexOf("::");
		return separator < 0 ? qualifiedName : qualifiedName.substring(separator + 2);
	}

	private void collectTraversalFunctions() {
		JsonArray targets = recipe.getAsJsonArray("targets");
		for (JsonElement targetElement : targets) {
			JsonObject target = targetElement.getAsJsonObject();
			String targetId = target.get("id").getAsString();
			JsonObject exports = target.getAsJsonObject("exports");
			int callersDepth = exports.has("callersDepth")
				? exports.get("callersDepth").getAsInt() : 0;
			int calleesDepth = exports.has("calleesDepth")
				? exports.get("calleesDepth").getAsInt() : 0;
			for (Function function : targetMatches.getOrDefault(targetId, List.of())) {
				collectDirection(function, callersDepth, true, targetId, exports);
				collectDirection(function, calleesDepth, false, targetId, exports);
			}
		}
	}

	private void collectDirection(
			Function start,
			int maxDepth,
			boolean callers,
			String targetId,
			JsonObject exports) {
		if (maxDepth <= 0) {
			return;
		}
		Queue<TraversalNode> queue = new ArrayDeque<>();
		Set<Address> visited = new LinkedHashSet<>();
		queue.add(new TraversalNode(start, 0));
		visited.add(start.getEntryPoint());
		while (!queue.isEmpty() && !monitor.isCancelled()) {
			TraversalNode node = queue.remove();
			if (node.depth >= maxDepth) {
				continue;
			}
			Set<Function> adjacent = callers
				? node.function.getCallingFunctions(monitor)
				: node.function.getCalledFunctions(monitor);
			List<Function> sorted = new ArrayList<>(adjacent);
			sorted.sort(Comparator.comparing(function -> function.getEntryPoint().toString()));
			for (Function function : sorted) {
				if (!isExportableFunction(function)) {
					continue;
				}
				if (callEdgeCount >= budget("maxCallEdges")) {
					addGap(
						"BUDGET_EXCEEDED",
						evidenceId(node.function),
						"Call-edge budget was reached while traversing " + targetId + ".",
						"Raise maxCallEdges only after reviewing the recipe scope.");
					return;
				}
				callEdgeCount++;
				addFunction(
					function,
					(callers ? "caller" : "callee") + " traversal for " + targetId,
					exports);
				if (visited.add(function.getEntryPoint())) {
					queue.add(new TraversalNode(function, node.depth + 1));
				}
			}
		}
	}

	private void addFunction(
			Function function,
			String context,
			JsonObject requestedExports) {
		if (!isExportableFunction(function)) {
			return;
		}
		String evidenceId = evidenceId(function);
		if (functionsByEvidenceId.containsKey(evidenceId)) {
			mergeExports(evidenceId, requestedExports);
			return;
		}
		if (functionsByEvidenceId.size() >= budget("maxFunctions")) {
			addGap(
				"BUDGET_EXCEEDED",
				evidenceId,
				"Function budget was reached while adding " + context + ".",
				"Raise maxFunctions only after reviewing the recipe scope.");
			return;
		}
		functionsByEvidenceId.put(evidenceId, function);
		mergeExports(evidenceId, requestedExports);
	}

	private void mergeExports(String evidenceId, JsonObject requestedExports) {
		JsonObject merged = exportsByEvidenceId.computeIfAbsent(
			evidenceId,
			ignored -> new JsonObject());
		for (String key : List.of("decompile", "constants", "branches")) {
			boolean enabled = merged.has(key) && merged.get(key).getAsBoolean();
			boolean requested = requestedExports != null &&
				requestedExports.has(key) &&
				requestedExports.get(key).getAsBoolean();
			merged.addProperty(key, enabled || requested);
		}
	}

	private JsonObject fullFunctionExports() {
		JsonObject exports = new JsonObject();
		exports.addProperty("decompile", true);
		exports.addProperty("constants", true);
		exports.addProperty("branches", true);
		return exports;
	}

	private JsonArray resolveVtableQueries() {
		JsonArray results = new JsonArray();
		if (!recipe.has("vtableQueries")) {
			return results;
		}
		for (JsonElement queryElement : recipe.getAsJsonArray("vtableQueries")) {
			JsonObject query = queryElement.getAsJsonObject();
			String className = query.get("className").getAsString();
			String expectedVftableName =
				normalizeQualifiedName(className) + "/vftable";
			long slotOffset = Long.decode(query.get("slotOffset").getAsString());
			JsonArray candidates = new JsonArray();
			List<Function> accepted = new ArrayList<>();
			SymbolIterator symbols = currentProgram.getSymbolTable().getAllSymbols(true);
			while (symbols.hasNext() && !monitor.isCancelled()) {
				Symbol symbol = symbols.next();
				String qualifiedName = symbol.getName(true);
				String normalized = normalizeQualifiedName(qualifiedName);
				if (!normalized.equals(expectedVftableName)) {
					continue;
				}
				JsonObject candidate = new JsonObject();
				candidate.addProperty("symbolName", symbol.getName());
				candidate.addProperty("qualifiedName", qualifiedName);
				candidate.addProperty("vtableAddress", symbol.getAddress().toString());
				Address slotAddress = symbol.getAddress().add(slotOffset);
				candidate.addProperty("slotAddress", slotAddress.toString());
				String rejection = symbol.getName().equalsIgnoreCase("vftable")
					? ""
					: "Symbol is vtable metadata rather than the primary vftable.";
				Function target = null;
				if (rejection.isEmpty()) {
					try {
						Memory memory = currentProgram.getMemory();
						long pointer = memory.getLong(slotAddress);
						Address targetAddress = currentProgram.getAddressFactory()
							.getDefaultAddressSpace().getAddress(pointer);
						candidate.addProperty("rawPointer", String.format("0x%016X", pointer));
						candidate.addProperty("targetAddress", targetAddress.toString());
						target = currentProgram.getFunctionManager().getFunctionAt(targetAddress);
						if (!isExportableFunction(target)) {
							rejection = "Vtable slot does not point to a defined function.";
							target = null;
						}
						else if (accepted.size() >= budget("maxVtableMatches")) {
							rejection = "Vtable match budget was reached.";
						}
					}
					catch (Exception exception) {
						rejection = "Could not read vtable slot: " + exception;
					}
				}
				boolean isAccepted = target != null && rejection.isEmpty();
				candidate.addProperty("accepted", isAccepted);
				candidate.addProperty("rejectionReason", rejection);
				if (target != null) {
					candidate.addProperty("evidenceId", evidenceId(target));
					candidate.add("target", functionIdentity(target));
				}
				if (isAccepted) {
					accepted.add(target);
					addFunction(
						target,
						"vtable query " + query.get("id").getAsString(),
						fullFunctionExports());
					JsonObject slot = new JsonObject();
					slot.addProperty("queryId", query.get("id").getAsString());
					slot.addProperty("ownerType", className);
					slot.addProperty("slot", (int) (slotOffset / 8));
					slot.addProperty("slotOffset", String.format("0x%X", slotOffset));
					slot.addProperty("status", "CONFIRMED");
					slot.addProperty(
						"confidence",
						pdbLoaded ? "HIGH" : "MEDIUM");
					slot.addProperty("targetEvidenceId", evidenceId(target));
					vtableSlotsByEvidenceId
						.computeIfAbsent(evidenceId(target), ignored -> new JsonArray())
						.add(slot);
				}
				candidates.add(candidate);
			}
			JsonObject result = new JsonObject();
			result.addProperty("queryId", query.get("id").getAsString());
			result.addProperty("className", className);
			result.addProperty("slotOffset", query.get("slotOffset").getAsString());
			result.addProperty("expectedMatches", query.get("expectedMatches").getAsInt());
			result.addProperty("matchCount", accepted.size());
			result.add("resolvedEvidenceIds", evidenceIds(accepted));
			result.add("candidates", candidates);
			result.addProperty(
				"status",
				accepted.size() == query.get("expectedMatches").getAsInt()
					? "CONFIRMED" : "COUNT_MISMATCH");
			results.add(result);
		}
		return results;
	}

	private void exportCollectedFunctions() {
		List<Function> functions = new ArrayList<>(functionsByEvidenceId.values());
		functions.sort(Comparator.comparing(this::formatRva));
		DecompInterface decompiler = createDecompiler();
		try {
			for (Function function : functions) {
				if (monitor.isCancelled()) {
					break;
				}
				exportedFunctions.put(evidenceId(function), exportFunction(function, decompiler));
			}
		}
		finally {
			decompiler.dispose();
		}
	}

	private JsonObject exportFunction(Function function, DecompInterface decompiler) {
		JsonObject json = functionIdentity(function);
		JsonObject exports = exportsByEvidenceId.get(evidenceId(function));
		boolean includeDecompile = exportEnabled(exports, "decompile");
		boolean includeConstants = exportEnabled(exports, "constants");
		boolean includeBranches = exportEnabled(exports, "branches");
		json.addProperty("owner", function.getParentNamespace().getName(true));
		json.addProperty("symbolSource", function.getSymbol().getSource().toString());
		json.addProperty("status", "CONFIRMED");
		json.addProperty(
			"confidence",
			pdbLoaded ? "pdb-symbol-plus-decompiler" : "binary-analysis");

		JsonArray parameters = new JsonArray();
		for (Parameter parameter : function.getParameters()) {
			JsonObject row = new JsonObject();
			row.addProperty("ordinal", parameter.getOrdinal());
			row.addProperty("name", parameter.getName());
			row.addProperty("dataType", parameter.getDataType().getDisplayName());
			row.addProperty("type", parameter.getDataType().getDisplayName());
			row.addProperty("storage", parameter.getVariableStorage().toString());
			parameters.add(row);
		}
		json.add("parameters", parameters);
		JsonObject returns = new JsonObject();
		returns.addProperty("dataType", function.getReturnType().getDisplayName());
		json.add("returns", returns);

		JsonArray incoming = new JsonArray();
		JsonArray callers = new JsonArray();
		Set<String> callerIds = new LinkedHashSet<>();
		ReferenceIterator references =
			currentProgram.getReferenceManager().getReferencesTo(function.getEntryPoint());
		while (references.hasNext() && !monitor.isCancelled()) {
			Reference reference = references.next();
			if (!reference.getReferenceType().isCall()) {
				continue;
			}
			JsonObject call = new JsonObject();
			call.addProperty("fromAddress", reference.getFromAddress().toString());
			call.addProperty("referenceType", reference.getReferenceType().getName());
			Function caller = currentProgram.getFunctionManager()
				.getFunctionContaining(reference.getFromAddress());
			if (isExportableFunction(caller)) {
				call.addProperty("callerEvidenceId", evidenceId(caller));
				call.addProperty("callerQualifiedName", caller.getName(true));
				if (callerIds.add(evidenceId(caller))) {
					callers.add(functionIdentity(caller));
				}
			}
			incoming.add(call);
		}
		json.add("callSites", incoming);
		json.add("incomingCallers", callers);

		JsonArray calledFunctions = new JsonArray();
		JsonArray calls = new JsonArray();
		List<Function> callees = new ArrayList<>(function.getCalledFunctions(monitor));
		callees.removeIf(callee -> !isExportableFunction(callee));
		callees.sort(Comparator.comparing(this::formatRva));
		for (Function callee : callees) {
			calledFunctions.add(functionIdentity(callee));
			JsonObject edge = new JsonObject();
			edge.addProperty("callerEvidenceId", evidenceId(function));
			edge.addProperty("calleeEvidenceId", evidenceId(callee));
			edge.addProperty("targetEvidenceId", evidenceId(callee));
			edge.addProperty("callsiteRva", "");
			edge.addProperty("kind", "DIRECT_OR_RECOVERED_CALL");
			edge.addProperty("status", "CONFIRMED");
			edge.addProperty(
				"confidence",
				pdbLoaded ? "HIGH" : "MEDIUM");
			calls.add(edge);
		}
		json.add("calledFunctions", calledFunctions);
		json.add("calls", calls);

		JsonArray numericConstants = new JsonArray();
		JsonArray branches = new JsonArray();
		InstructionIterator instructions =
			currentProgram.getListing().getInstructions(function.getBody(), true);
		while (instructions.hasNext() && !monitor.isCancelled()) {
			Instruction instruction = instructions.next();
			if (includeBranches && instruction.getFlowType().isConditional()) {
				JsonObject branch = new JsonObject();
				branch.addProperty("address", instruction.getAddress().toString());
				branch.addProperty("instruction", instruction.toString());
				branches.add(branch);
			}
			if (!includeConstants) {
				continue;
			}
			for (int operand = 0; operand < instruction.getNumOperands(); operand++) {
				for (Object object : instruction.getOpObjects(operand)) {
					if (!(object instanceof Scalar)) {
						continue;
					}
					if (constantCount >= budget("maxConstants")) {
						if (!constantBudgetGapRecorded) {
							addGap(
								"BUDGET_EXCEEDED",
								evidenceId(function),
								"Numeric-constant budget was reached.",
								"Raise maxConstants only after reviewing the recipe scope.");
							constantBudgetGapRecorded = true;
						}
						continue;
					}
					Scalar scalar = (Scalar) object;
					JsonObject constant = new JsonObject();
					constant.addProperty("address", instruction.getAddress().toString());
					constant.addProperty("value", scalar.getSignedValue());
					constant.addProperty(
						"unsignedHex",
						String.format("0x%X", scalar.getUnsignedValue()));
					constant.addProperty("bitLength", scalar.bitLength());
					constant.addProperty("instruction", instruction.toString());
					constant.addProperty("valueType", "integer");
					constant.addProperty("context", instruction.toString());
					constant.addProperty("status", "CONFIRMED");
					constant.addProperty(
						"confidence",
						pdbLoaded ? "HIGH" : "MEDIUM");
					numericConstants.add(constant);
					constantCount++;
				}
			}
		}
		json.add("numericConstants", numericConstants);
		json.add("constants", numericConstants.deepCopy());
		json.add("stringConstants", new JsonArray());
		json.add("branches", branches);
		json.add("fieldAccesses", new JsonArray());
		JsonArray vtableSlots = vtableSlotsByEvidenceId.get(evidenceId(function));
		json.add(
			"vtableSlots",
			vtableSlots == null ? new JsonArray() : vtableSlots.deepCopy());
		json.add("gaps", new JsonArray());

		if (!includeDecompile) {
			JsonObject decompile = new JsonObject();
			decompile.addProperty("completed", false);
			decompile.addProperty("skipped", true);
			json.add("decompile", decompile);
			return json;
		}
		DecompileResults results =
			decompiler.decompileFunction(function, DECOMPILE_TIMEOUT_SECONDS, monitor);
		boolean completed = results != null && results.decompileCompleted() &&
			results.getDecompiledFunction() != null;
		json.addProperty("decompileCompleted", completed);
		JsonObject decompile = new JsonObject();
		decompile.addProperty("completed", completed);
		if (completed) {
			String source = results.getDecompiledFunction().getC();
			int perFunctionLimit = budget("maxDecompiledCharactersPerFunction");
			int remaining = Math.max(
				0,
				budget("maxTotalDecompiledCharacters") - totalDecompiledCharacters);
			int limit = Math.min(perFunctionLimit, remaining);
			boolean truncated = source.length() > limit;
			String exportedSource = source.substring(0, Math.min(source.length(), limit));
			json.addProperty("decompiledC", exportedSource);
			decompile.addProperty("c", exportedSource);
			decompile.addProperty("truncated", truncated);
			totalDecompiledCharacters += exportedSource.length();
			if (truncated) {
				addFunctionGap(
					json,
					"BUDGET_EXCEEDED",
					"Decompiler text was truncated by the recipe budget.",
					"Raise a decompiler character budget only after reviewing the target.");
			}
		}
		else {
			String error = results == null ? "No decompiler result" : results.getErrorMessage();
			json.addProperty("decompileError", error);
			decompile.addProperty("error", error);
			addFunctionGap(
				json,
				"DECOMPILE_FAILED",
				error,
				"Inspect the Ghidra function and analyzer log.");
		}
		json.add("decompile", decompile);
		return json;
	}

	private JsonArray resolveFieldQueries() {
		JsonArray results = new JsonArray();
		if (!recipe.has("fieldQueries")) {
			return results;
		}
		for (JsonElement queryElement : recipe.getAsJsonArray("fieldQueries")) {
			JsonObject query = queryElement.getAsJsonObject();
			String queryId = query.get("id").getAsString();
			String structureName = query.get("structureName").getAsString();
			String fieldName = query.get("fieldName").getAsString();
			DataTypeComponent field = findField(structureName, fieldName);
			JsonArray candidates = new JsonArray();
			List<Function> accepted = new ArrayList<>();
			List<Function> functions = queryFunctions(query);
			for (Function function : functions) {
				JsonObject candidate = functionIdentity(function);
				String rejection;
				JsonArray instructions = new JsonArray();
				if (field == null) {
					rejection = "PDB data type field was not found.";
				}
				else {
					instructions = matchingFieldInstructions(
						function,
						field.getOffset(),
						Math.max(field.getLength(), 1));
					boolean decompilerNamesField =
						decompilerReferencesField(function, fieldName);
					boolean isMatch = instructions.size() > 0 || decompilerNamesField;
					rejection = isMatch
						? ""
						: "No instruction or decompiler expression referenced the field.";
					candidate.addProperty(
						"fieldOffset",
						String.format("0x%X", field.getOffset()));
					candidate.addProperty("fieldLength", field.getLength());
					candidate.add("instructions", instructions);
					candidate.addProperty("decompilerNamesField", decompilerNamesField);
				}
				boolean isAccepted = rejection.isEmpty() &&
					fieldAccessCount < budget("maxFieldAccesses");
				if (rejection.isEmpty() && !isAccepted) {
					rejection = "Field-access match budget was reached.";
				}
				candidate.addProperty("accepted", isAccepted);
				candidate.addProperty("rejectionReason", rejection);
				if (isAccepted) {
					accepted.add(function);
					fieldAccessCount++;
					JsonObject access = new JsonObject();
					access.addProperty("queryId", queryId);
					access.addProperty("structureName", structureName);
					access.addProperty("ownerType", structureName);
					access.addProperty("fieldName", fieldName);
					access.addProperty(
						"fieldOffset",
						String.format("0x%X", field.getOffset()));
					access.addProperty(
						"offset",
						String.format("0x%X", field.getOffset()));
					access.addProperty("access", "READ_OR_WRITE");
					access.addProperty("status", "CONFIRMED");
					access.addProperty(
						"confidence",
						pdbLoaded ? "HIGH" : "MEDIUM");
					access.add("instructions", instructions.deepCopy());
					exportedFunctions.get(evidenceId(function))
						.getAsJsonArray("fieldAccesses").add(access);
				}
				candidates.add(candidate);
			}
			JsonObject result = new JsonObject();
			result.addProperty("queryId", queryId);
			result.addProperty("structureName", structureName);
			result.addProperty("fieldName", fieldName);
			result.add(
				"functionTargetIds",
				query.has("functionTargetIds")
					? query.getAsJsonArray("functionTargetIds").deepCopy()
					: new JsonArray());
			result.addProperty("expectedMatches", query.get("expectedMatches").getAsInt());
			result.addProperty("matchCount", accepted.size());
			result.add("resolvedEvidenceIds", evidenceIds(accepted));
			result.add("candidates", candidates);
			result.addProperty(
				"status",
				accepted.size() == query.get("expectedMatches").getAsInt()
					? "CONFIRMED" : "COUNT_MISMATCH");
			results.add(result);
		}
		return results;
	}

	private List<Function> queryFunctions(JsonObject query) {
		Map<String, Function> unique = new LinkedHashMap<>();
		if (query.has("functionTargetIds")) {
			for (JsonElement targetId : query.getAsJsonArray("functionTargetIds")) {
				for (Function function :
						targetMatches.getOrDefault(targetId.getAsString(), List.of())) {
					unique.put(evidenceId(function), function);
				}
			}
		}
		else {
			unique.putAll(functionsByEvidenceId);
		}
		return new ArrayList<>(unique.values());
	}

	private DataTypeComponent findField(String requestedStructure, String fieldName) {
		Iterator<DataType> dataTypes =
			currentProgram.getDataTypeManager().getAllDataTypes();
		String requested = normalizeQualifiedName(requestedStructure);
		while (dataTypes.hasNext() && !monitor.isCancelled()) {
			DataType dataType = dataTypes.next();
			if (!(dataType instanceof Structure)) {
				continue;
			}
			String name = normalizeQualifiedName(dataType.getName());
			String path = normalizeQualifiedName(dataType.getPathName());
			if (!name.equals(requested) && !path.endsWith(requested)) {
				continue;
			}
			Structure structure = (Structure) dataType;
			for (DataTypeComponent component : structure.getDefinedComponents()) {
				if (fieldName.equals(component.getFieldName())) {
					return component;
				}
			}
		}
		return null;
	}

	private JsonArray matchingFieldInstructions(
			Function function,
			int offset,
			int length) {
		JsonArray matches = new JsonArray();
		InstructionIterator instructions =
			currentProgram.getListing().getInstructions(function.getBody(), true);
		while (instructions.hasNext() && !monitor.isCancelled()) {
			Instruction instruction = instructions.next();
			boolean found = false;
			for (int operand = 0; operand < instruction.getNumOperands(); operand++) {
				String operandRepresentation =
					instruction.getDefaultOperandRepresentation(operand);
				boolean memoryOperand =
					operandRepresentation.contains("[") &&
					operandRepresentation.contains("]");
				boolean stackOrFrameOperand = Pattern
					.compile("(?i)\\b(?:RSP|RBP)\\b")
					.matcher(operandRepresentation)
					.find();
				if (!memoryOperand || stackOrFrameOperand) {
					continue;
				}
				for (Object object : instruction.getOpObjects(operand)) {
					if (object instanceof Scalar) {
						long value = ((Scalar) object).getUnsignedValue();
						if (offset <= value && value < offset + length) {
							found = true;
						}
					}
				}
			}
			if (found) {
				JsonObject row = new JsonObject();
				row.addProperty("address", instruction.getAddress().toString());
				row.addProperty("instruction", instruction.toString());
				matches.add(row);
			}
		}
		return matches;
	}

	private String decompiledText(Function function) {
		JsonObject exported = exportedFunctions.get(evidenceId(function));
		if (exported == null || !exported.has("decompiledC")) {
			return "";
		}
		return exported.get("decompiledC").getAsString();
	}

	private boolean decompilerReferencesField(
			Function function,
			String fieldName) {
		Pattern memberExpression = Pattern.compile(
			"(?:->|\\.)\\s*" + Pattern.quote(fieldName) + "\\b");
		return memberExpression.matcher(decompiledText(function)).find();
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

	private boolean exportEnabled(JsonObject exports, String name) {
		return exports != null &&
			exports.has(name) &&
			exports.get(name).getAsBoolean();
	}

	private boolean isExportableFunction(Function function) {
		if (function == null || function.isExternal()) {
			return false;
		}
		Address entryPoint = function.getEntryPoint();
		return entryPoint != null &&
			entryPoint.isMemoryAddress() &&
			entryPoint.getAddressSpace().equals(
				currentProgram.getImageBase().getAddressSpace());
	}

	private JsonObject functionIdentity(Function function) {
		JsonObject json = new JsonObject();
		json.addProperty("evidenceId", evidenceId(function));
		json.addProperty("name", function.getName());
		json.addProperty("qualifiedName", function.getName(true));
		json.addProperty("entryPoint", function.getEntryPoint().toString());
		json.addProperty("rva", formatRva(function));
		json.addProperty("signature", function.getPrototypeString(true, true));
		json.addProperty("canonicalSignature", canonicalSignature(function));
		return json;
	}

	private String evidenceId(Function function) {
		return "native://" + binarySha + "/" + currentProgram.getName() + "/" +
			formatRva(function);
	}

	private String formatRva(Function function) {
		if (!isExportableFunction(function)) {
			throw new IllegalArgumentException(
				"Cannot compute an RVA for an external, non-memory, or foreign-address-space function.");
		}
		long rva = function.getEntryPoint().subtract(currentProgram.getImageBase());
		return String.format("0x%X", rva);
	}

	private JsonArray evidenceIds(Collection<Function> functions) {
		JsonArray ids = new JsonArray();
		for (Function function : functions) {
			ids.add(evidenceId(function));
		}
		return ids;
	}

	private int budget(String name) {
		return budgets.get(name).getAsInt();
	}

	private String normalizeQualifiedName(String name) {
		return canonicalQualifiedName(name).toLowerCase(Locale.ROOT);
	}

	private String canonicalQualifiedName(String name) {
		return name.replace('\\', '/')
			.replace("::", "/")
			.replaceAll("/+", "/")
			.replaceAll("^/+", "");
	}

	private void addFunctionGap(
			JsonObject function,
			String reasonCode,
			String detail,
			String nextProbe) {
		JsonObject gap = new JsonObject();
		gap.addProperty(
			"gapId",
			"native-gap://recipe/function/" + formatGapOrdinal(gaps.size()));
		gap.addProperty("functionEvidenceId", function.get("evidenceId").getAsString());
		gap.addProperty("status", "NOT_RECOVERED");
		gap.addProperty("reasonCode", reasonCode);
		gap.addProperty("detail", detail);
		gap.addProperty("nextProbe", nextProbe);
		function.getAsJsonArray("gaps").add(gap.deepCopy());
	}

	private void addGap(
			String reasonCode,
			String functionEvidenceId,
			String detail,
			String nextProbe) {
		JsonObject gap = new JsonObject();
		gap.addProperty(
			"gapId",
			"native-gap://recipe/global/" + formatGapOrdinal(gaps.size()));
		gap.addProperty("functionEvidenceId", functionEvidenceId);
		gap.addProperty("status", "NOT_RECOVERED");
		gap.addProperty("reasonCode", reasonCode);
		gap.addProperty("detail", detail);
		gap.addProperty("nextProbe", nextProbe);
		gaps.add(gap);
	}

	private String formatGapOrdinal(int ordinal) {
		return String.format("%04d", ordinal);
	}

	private boolean isPdbLoaded() {
		Options options = currentProgram.getOptions(Program.PROGRAM_INFO);
		return options.getBoolean(PdbParserConstants.PDB_LOADED, false);
	}

	private static final class TraversalNode {
		private final Function function;
		private final int depth;

		private TraversalNode(Function function, int depth) {
			this.function = function;
			this.depth = depth;
		}
	}
}
