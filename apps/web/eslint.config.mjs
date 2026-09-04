import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";

export default defineConfig([
  ...nextVitals,
  globalIgnores([".next/**", "test-results/**", "playwright-report/**", "next-env.d.ts"]),
  // The existing client pages load their local API in effects. React Compiler
  // is not enabled; retain this established data-loading pattern.
  { rules: { "react-hooks/set-state-in-effect": "off" } }
]);
