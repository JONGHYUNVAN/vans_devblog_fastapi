// 독립 기준: TypeScript 컴파일러 API 로 import 와 실제 모듈 해석을 구한다. 분석기 코드를 쓰지 않는다.
// node ts_truth.js <typescript lib> <repoDir> <analyzer json> <out json>
const fs = require('fs');
const path = require('path');
const [, , tsLib, repoDir, analyzerJson, outJson] = process.argv;
const ts = require(tsLib);

const analyzer = JSON.parse(fs.readFileSync(analyzerJson, 'utf8'));
const files = analyzer.nodes.map((n) => n.id).filter((f) => /\.tsx?$/.test(f));
const cfgPath = ts.findConfigFile(repoDir, ts.sys.fileExists, 'tsconfig.json');
const cfg = ts.readConfigFile(cfgPath, ts.sys.readFile);
const options = ts.parseJsonConfigFileContent(cfg.config, ts.sys, repoDir).options;

const rel = (abs) => path.relative(repoDir, abs).split(path.sep).join('/');
const resolve = (spec, from) => {
  const r = ts.resolveModuleName(spec, from, options, ts.sys).resolvedModule;
  return r ? r.resolvedFileName : null;
};

// transpile 결과에 남은 import = 실제 실행 때 불리는 import. 타입으로만 쓴 import 는 컴파일러가 지운다.
function runtimeSpecs(abs, text) {
  const out = ts.transpileModule(text, {
    fileName: abs,
    compilerOptions: { target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.Preserve, verbatimModuleSyntax: false },
  }).outputText;
  const sf = ts.createSourceFile('o.tsx', out, ts.ScriptTarget.ESNext, false, ts.ScriptKind.TSX);
  const specs = new Set();
  sf.forEachChild((n) => {
    if ((ts.isImportDeclaration(n) || ts.isExportDeclaration(n)) && n.moduleSpecifier) specs.add(n.moduleSpecifier.text);
  });
  return specs;
}

const edges = [];
const unresolvedDynamic = [];
for (const file of files) {
  const abs = path.join(repoDir, file);
  const text = fs.readFileSync(abs, 'utf8');
  const kind = file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(abs, text, ts.ScriptTarget.ESNext, true, kind);
  const runtime = runtimeSpecs(abs, text);
  const add = (spec, how) => {
    const resolved = resolve(spec, abs);
    if (!resolved || resolved.includes('node_modules')) return;
    edges.push({ source: file, target: rel(resolved), spec, how, runtime: how === 'dynamic' || how === 'require' || runtime.has(spec) });
  };
  const visit = (n) => {
    if (ts.isImportDeclaration(n)) add(n.moduleSpecifier.text, n.importClause && n.importClause.isTypeOnly ? 'type-keyword' : 'static');
    else if (ts.isExportDeclaration(n) && n.moduleSpecifier) add(n.moduleSpecifier.text, n.isTypeOnly ? 'type-keyword' : 'static');
    else if (ts.isImportTypeNode(n) && ts.isLiteralTypeNode(n.argument) && ts.isStringLiteralLike(n.argument.literal)) add(n.argument.literal.text, 'import-type-node');
    else if (ts.isCallExpression(n) && n.expression.kind === ts.SyntaxKind.ImportKeyword) {
      const a = n.arguments[0];
      if (a && ts.isStringLiteralLike(a)) add(a.text, 'dynamic');
      else unresolvedDynamic.push({ file, text: a ? a.getText().slice(0, 70) : '' });
    } else if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'require' && n.arguments[0] && ts.isStringLiteralLike(n.arguments[0])) add(n.arguments[0].text, 'require');
    ts.forEachChild(n, visit);
  };
  visit(sf);
}
fs.writeFileSync(outJson, JSON.stringify({ tsconfig: rel(cfgPath), files: files.length, edges, unresolvedDynamic }, null, 1));
console.log(rel(cfgPath), 'files', files.length, 'edges', edges.length, 'unresolvedDynamic', unresolvedDynamic.length);
