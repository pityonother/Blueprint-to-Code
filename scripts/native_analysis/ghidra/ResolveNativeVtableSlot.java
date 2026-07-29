// Resolve a native virtual-table slot to the PDB-backed function stored there.
//@category BlueprintToCode

import java.io.File;
import java.io.FileWriter;
import java.time.Instant;
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
import ghidra.program.model.listing.Program;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolIterator;

public class ResolveNativeVtableSlot extends GhidraScript {

	@Override
	protected void run() throws Exception {
		String[] args = getScriptArgs();
		if (args.length != 3) {
			throw new IllegalArgumentException(
				"ResolveNativeVtableSlot.java expects output JSON, class-name substring, and slot offset");
		}

		File outputFile = new File(args[0]).getAbsoluteFile();
		File parent = outputFile.getParentFile();
		if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
			throw new IllegalStateException("Could not create output directory: " + parent);
		}

		String classNeedle = args[1].toLowerCase(Locale.ROOT);
		long slotOffset = Long.decode(args[2]);
		String binarySha = currentProgram.getExecutableSHA256();
		if (binarySha == null || binarySha.isBlank()) {
			binarySha = "unknown-sha256";
		}
		binarySha = binarySha.toLowerCase(Locale.ROOT);

		JsonObject root = new JsonObject();
		root.addProperty("schema", "blueprint-to-code-native-vtable-slot/v1");
		root.addProperty("generatedAtUtc", Instant.now().toString());
		root.addProperty("program", currentProgram.getName());
		root.addProperty("binarySha256", binarySha);
		root.addProperty("imageBase", currentProgram.getImageBase().toString());
		root.addProperty("pdbLoaded", isPdbLoaded());
		root.addProperty("classNeedle", args[1]);
		root.addProperty("slotOffset", String.format("0x%X", slotOffset));

		JsonArray matches = new JsonArray();
		SymbolIterator iterator = currentProgram.getSymbolTable().getAllSymbols(true);
		while (iterator.hasNext() && !monitor.isCancelled()) {
			Symbol symbol = iterator.next();
			String qualifiedName = symbol.getName(true);
			String lowerName = qualifiedName.toLowerCase(Locale.ROOT);
			if (!lowerName.contains(classNeedle) || !lowerName.contains("vftable")) {
				continue;
			}
			matches.add(resolveSlot(symbol, slotOffset, binarySha));
		}
		root.addProperty("matchCount", matches.size());
		root.add("matches", matches);

		Gson gson = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
		try (FileWriter writer = new FileWriter(outputFile)) {
			gson.toJson(root, writer);
		}

		println("Resolved " + matches.size() + " matching vftables in " + outputFile);
	}

	private JsonObject resolveSlot(Symbol symbol, long slotOffset, String binarySha) {
		JsonObject json = new JsonObject();
		Address vtableAddress = symbol.getAddress();
		Address slotAddress = vtableAddress.add(slotOffset);
		json.addProperty("symbolName", symbol.getName());
		json.addProperty("qualifiedName", symbol.getName(true));
		json.addProperty("vtableAddress", vtableAddress.toString());
		json.addProperty("slotAddress", slotAddress.toString());

		try {
			Memory memory = currentProgram.getMemory();
			long pointerValue = memory.getLong(slotAddress);
			Address targetAddress =
				currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(pointerValue);
			json.addProperty("rawPointer", String.format("0x%016X", pointerValue));
			json.addProperty("targetAddress", targetAddress.toString());

			Function target = currentProgram.getFunctionManager().getFunctionAt(targetAddress);
			if (target != null) {
				long rva = targetAddress.subtract(currentProgram.getImageBase());
				String rvaText = String.format("0x%X", rva);
				json.addProperty(
					"evidenceId",
					"native://" + binarySha + "/" + currentProgram.getName() + "/" + rvaText);
				json.addProperty("targetName", target.getName());
				json.addProperty("targetQualifiedName", target.getName(true));
				json.addProperty("targetRva", rvaText);
				json.addProperty("targetSignature", target.getPrototypeString(true, true));
			}
			else {
				json.addProperty("targetFunctionFound", false);
			}
		}
		catch (Exception exception) {
			json.addProperty("error", exception.toString());
		}
		return json;
	}

	private boolean isPdbLoaded() {
		Options options = currentProgram.getOptions(Program.PROGRAM_INFO);
		return options.getBoolean(PdbParserConstants.PDB_LOADED, false);
	}
}
