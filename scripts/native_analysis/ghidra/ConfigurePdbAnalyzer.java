// Configure local PDB discovery before Ghidra's auto-analysis.
//@category BlueprintToCode

import java.util.Map;

import ghidra.app.script.GhidraScript;
import ghidra.app.util.bin.format.pdb.PdbParserConstants;
import ghidra.framework.options.Options;
import ghidra.program.model.listing.Program;

public class ConfigurePdbAnalyzer extends GhidraScript {

	@Override
	protected void run() throws Exception {
		String[] args = getScriptArgs();
		if (args.length != 1) {
			throw new IllegalArgumentException(
				"ConfigurePdbAnalyzer.java expects one argument: the local PDB directory");
		}

		Options programOptions = currentProgram.getOptions(Program.PROGRAM_INFO);
		if (programOptions.getBoolean(PdbParserConstants.PDB_LOADED, false)) {
			println("PDB is already loaded; keeping the project's recorded symbols.");
			return;
		}

		Map<String, String> options = getCurrentAnalysisOptionsAndValues(currentProgram);
		String repositoryOption = "PDB.Symbol Repository Path";
		if (!options.containsKey(repositoryOption)) {
			println(
				"Ghidra does not expose " + repositoryOption +
				"; using automatic PDB discovery from the PE import directory.");
			return;
		}

		setAnalysisOption(currentProgram, repositoryOption, args[0]);
		if (options.containsKey("PDB Universal")) {
			setAnalysisOption(currentProgram, "PDB Universal", "true");
		}

		println("Configured local PDB repository: " + args[0]);
	}
}
