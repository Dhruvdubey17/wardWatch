import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

/**
 * Requests without a handler fail the test, so every call a test makes is
 * declared. The one default is an empty census, which the inbox reads for names.
 */
export const server = setupServer(http.get("*/api/ward/census", () => HttpResponse.json([])));
