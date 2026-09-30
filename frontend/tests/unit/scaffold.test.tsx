import { render, screen } from "@testing-library/react";
import WardPage from "@/app/page";

test("renders the ward heading", () => {
  render(<WardPage />);
  expect(screen.getByRole("heading", { name: "Ward" })).toBeInTheDocument();
});
