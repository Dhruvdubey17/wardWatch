import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";
import prettier from "eslint-config-prettier";

const config = [
    ...nextVitals,
    ...nextTypescript,
    prettier,
    {
        ignores: [
            ".next/**",
            "coverage/**",
            "playwright-report/**",
            "test-results/**",
            "next-env.d.ts",
            "lib/api/schema.ts",
        ],
    },
];

export default config;
