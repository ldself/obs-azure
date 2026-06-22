/* ESLint config for the OBS SPA (strict TypeScript, no `any` — CLAUDE.md). */
module.exports = {
  root: true,
  env: { browser: true, es2021: true },
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
  ],
  parser: "@typescript-eslint/parser",
  parserOptions: { ecmaVersion: "latest", sourceType: "module" },
  plugins: ["@typescript-eslint", "react-hooks", "react-refresh"],
  settings: { react: { version: "detect" } },
  ignorePatterns: ["dist", ".eslintrc.cjs", "vite.config.ts"],
  rules: {
    "react-hooks/rules-of-hooks": "error",
    "react-hooks/exhaustive-deps": "warn",
    "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
    // RULE: no `any` in production code.
    "@typescript-eslint/no-explicit-any": "error",
  },
};
