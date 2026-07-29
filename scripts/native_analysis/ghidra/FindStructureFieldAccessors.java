// Find native instructions in a class namespace that reference a PDB structure field offset.
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
import ghidra.program.model.data.DataType;
import ghidra.program.model.data.DataTypeComponent;
import ghidra.program.model.data.Structure;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.listing.Program;
import ghidra.program.model.scalar.Scalar;

public class FindStructureFieldAccessors extends GhidraScript {

	private static final int MAX_INSTRUCTIONS_PER_FUNCTION = 100;

	@Override
	protected void run() throws Exception {
		String[] args = getScriptArgs();
		if (args.length != 4) {
			throw new IllegalArgumentException(
				"FindStructureFieldAccessors.java expects output JSON, structure name, field name, and function-name substring");
		}

		File outputFile = new File(args[0]).getAbsoluteFile();
		File parent = outputFile.getParentFile();
		if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
			throw new IllegalStateException("Could not create output directory: " + parent);
		}

		String structureName = args[1];
		String fieldName = args[2];
		String functionNeedle = args[3].toLowerCase(Locale.ROOT);
		DataTypeComponent field = findField(structureName, fieldName);
		int offsetStart = field.getOffset();
		int offsetEnd = offsetStart + Math.max(field.getLength(), 1) - 1;

		String binarySha = currentProgram.getExecutableSHA256();
		if (binarySha == null || binarySha.isBlank()) {
			binarySha = "unknown-sha256";
		}
		binarySha = binarySha.toLowerCase(Locale.ROOT);

		Map<String, JsonObject> functionsByEntryPoint = new LinkedHashMap<>();
		FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
		while (functions.hasNext() && !monitor.isCancelled()) {
			Function function = functions.next();
			if (!function.getName(true).toLowerCase(Locale.ROOT).contains(functionNeedle)) {
				continue;
			}

			JsonArray instructions = findMatchingInstructions(function, offsetStart, offsetEnd);
			if (instructions.size() == 0) {
				continue;
			}
			JsonObject functionJson = functionIdentity(function, binarySha);
			functionJson.addProperty("matchingInstructionCount", instructions.size());
			functionJson.add("instructions", instructions);
			functionsByEntryPoint.put(function.getEntryPoint().toString(), functionJson);
		}

		List<JsonObject> sortedFunctions = new ArrayList<>(functionsByEntryPoint.values());
		sortedFunctions.sort(Comparator.comparing(
			function -> function.get("qualifiedName").getAsString()));
		JsonArray matches = new JsonArray();
		for (JsonObject function : sortedFunctions) {
			matches.add(function);
		}

		JsonObject root = new JsonObject();
		root.addProperty("schema", "blueprint-to-code-native-structure-field-accessors/v1");
		root.addProperty("generatedAtUtc", Instant.now().toString());
		root.addProperty("program", currentProgram.getName());
		root.addProperty("binarySha256", binarySha);
		root.addProperty("imageBase", currentProgram.getImageBase().toString());
		root.addProperty("pdbLoaded", isPdbLoaded());
		root.addProperty("structureName", structureName);
		root.addProperty("fieldName", fieldName);
		root.addProperty("fieldOffset", String.format("0x%X", offsetStart));
		root.addProperty("fieldLength", field.getLength());
		root.addProperty("functionNeedle", args[3]);
		root.addProperty("matchCount", matches.size());
		root.add("functions", matches);

		Gson gson = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
		try (FileWriter writer = new FileWriter(outputFile)) {
			gson.toJson(root, writer);
		}

		println("Found " + matches.size() + " candidate field-accessor functions in " + outputFile);
	}

	private DataTypeComponent findField(String structureName, String fieldName) {
		List<DataType> candidates = new ArrayList<>();
		currentProgram.getDataTypeManager().findDataTypes(structureName, candidates);
		for (DataType candidate : candidates) {
			if (!(candidate instanceof Structure)) {
				continue;
			}
			Structure structure = (Structure) candidate;
			for (DataTypeComponent field : structure.getDefinedComponents()) {
				if (fieldName.equals(field.getFieldName())) {
					return field;
				}
			}
		}
		throw new IllegalArgumentException(
			"Could not find PDB structure field " + structureName + "::" + fieldName);
	}

	private JsonArray findMatchingInstructions(Function function, int offsetStart, int offsetEnd) {
		JsonArray matches = new JsonArray();
		InstructionIterator instructions =
			currentProgram.getListing().getInstructions(function.getBody(), true);
		while (instructions.hasNext() && !monitor.isCancelled() &&
				matches.size() < MAX_INSTRUCTIONS_PER_FUNCTION) {
			Instruction instruction = instructions.next();
			if (!referencesOffset(instruction, offsetStart, offsetEnd)) {
				continue;
			}
			JsonObject json = new JsonObject();
			json.addProperty("address", instruction.getAddress().toString());
			json.addProperty("text", instruction.toString());
			matches.add(json);
		}
		return matches;
	}

	private boolean referencesOffset(Instruction instruction, int offsetStart, int offsetEnd) {
		for (int operandIndex = 0; operandIndex < instruction.getNumOperands(); operandIndex++) {
			for (Object object : instruction.getOpObjects(operandIndex)) {
				if (!(object instanceof Scalar)) {
					continue;
				}
				long value = ((Scalar) object).getUnsignedValue();
				if (offsetStart <= value && value <= offsetEnd) {
					return true;
				}
			}
		}
		return false;
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
		return json;
	}

	private boolean isPdbLoaded() {
		Options options = currentProgram.getOptions(Program.PROGRAM_INFO);
		return options.getBoolean(PdbParserConstants.PDB_LOADED, false);
	}
}
