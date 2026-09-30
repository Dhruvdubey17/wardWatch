import { setupServer } from "msw/node";

/** Requests without a handler fail the test, so every call a test makes is declared. */
export const server = setupServer();
