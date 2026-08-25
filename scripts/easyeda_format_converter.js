#!/usr/bin/env node
"use strict";

// Thin deterministic adapter over the officially installed JLCEDA converter.
// Bulk KiCad/EasyEDA data stays on disk and only a compact receipt is printed.

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const childProcess = require("child_process");

function fail(message) {
  process.stderr.write(String(message) + "\n");
  process.exit(1);
}

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key || !key.startsWith("--") || value === undefined) fail("Expected --name value arguments");
    result[key.slice(2)] = value;
  }
  return result;
}

function digest(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex").toUpperCase();
}

function safeEntry(root, name) {
  const normalized = String(name).replaceAll("\\", "/");
  if (!normalized || normalized.startsWith("/") || normalized.split("/").includes("..")) {
    fail(`Unsafe EPro entry: ${name}`);
  }
  const target = path.resolve(root, ...normalized.split("/").filter(Boolean));
  if (target !== path.resolve(root) && !target.startsWith(path.resolve(root) + path.sep)) {
    fail(`EPro entry escapes staging directory: ${name}`);
  }
  return target;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const input = path.resolve(args.input || "");
  const output = path.resolve(args.output || "");
  const converterRoot = path.resolve(args["converter-root"] || "C:/Program Files/lceda-pro-format-converter");
  const projectName = args["project-name"] || "pcb-project";
  if (!fs.existsSync(input) || path.extname(input).toLowerCase() !== ".kicad_pcb") fail(`KiCad PCB not found: ${input}`);
  if (!output.toLowerCase().endsWith(".epro")) fail("Output must use the .epro suffix");

  const modulePath = path.join(converterRoot, "resources", "node_modules", "chamelon");
  const packagePath = path.join(modulePath, "package.json");
  const sevenZip = path.join(converterRoot, "resources", "tools", "7z", "x64", "7za.exe");
  if (!fs.existsSync(packagePath) || !fs.existsSync(sevenZip)) fail(`Official converter installation is incomplete: ${converterRoot}`);

  const official = require(modulePath);
  const quiet = new official.Logger({ level: 4 });
  const source = fs.readFileSync(input, "utf8").replace(/^\uFEFF/, "");
  const params = {
    type: "kicad",
    target: "easyeda-pro",
    datas: [{ fileName: path.basename(input), source }],
    options: { projectName, description: projectName },
  };
  const converted = official.formatConvert(params, { logger: quiet });
  if (!converted || converted.success !== true || !converted.result) {
    fail(`Official format conversion failed: ${JSON.stringify({ code: converted?.code, message: converted?.message })}`);
  }
  const result = converted.result;
  const entries = official.convertChamelonJsonToEpro(result, {});
  if (!entries || typeof entries !== "object" || !Object.prototype.hasOwnProperty.call(entries, "project.json")) {
    fail("Official converter did not produce a valid EPro entry set");
  }

  fs.mkdirSync(path.dirname(output), { recursive: true });
  const staging = fs.mkdtempSync(path.join(os.tmpdir(), "easyeda-epro-"));
  try {
    for (const [name, value] of Object.entries(entries)) {
      const target = safeEntry(staging, name);
      if (name.endsWith("/")) {
        fs.mkdirSync(target, { recursive: true });
      } else if (typeof value === "string" || Buffer.isBuffer(value)) {
        fs.mkdirSync(path.dirname(target), { recursive: true });
        fs.writeFileSync(target, value, typeof value === "string" ? "utf8" : undefined);
      }
    }
    if (fs.existsSync(output)) fs.rmSync(output);
    const archived = childProcess.spawnSync(sevenZip, ["a", "-tzip", output, ".\\*"], {
      cwd: staging,
      windowsHide: true,
      encoding: "utf8",
    });
    if (archived.status !== 0 || !fs.existsSync(output)) {
      fail(`Official EPro archive creation failed (${archived.status}): ${archived.stderr || archived.stdout || ""}`);
    }
  } finally {
    fs.rmSync(staging, { recursive: true, force: true });
  }

  const receipt = {
    schema: 1,
    kind: "easyeda_official_format_conversion",
    success: true,
    converter: "JLCEDA format converter / chamelon",
    converterVersion: "1.10.1",
    sourceSha256: digest(input),
    outputSha256: digest(output),
    projectCount: 1,
    pcbCount: Array.isArray(result.pcbs) ? result.pcbs.length : 0,
    footprintCount: Array.isArray(result.footprints) ? result.footprints.length : 0,
    deviceCount: Array.isArray(result.devices) ? result.devices.length : 0,
    entryCount: Object.keys(entries).length,
  };
  process.stdout.write(JSON.stringify(receipt) + "\n");
  process.exit(0);
}

main();
