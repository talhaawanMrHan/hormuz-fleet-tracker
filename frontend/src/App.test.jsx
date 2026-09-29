import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// jsdom doesn't implement enough of SVG/Canvas for Leaflet's vector renderer
// to mount/unmount cleanly (a well-known jsdom limitation, not specific to
// this app). MapView is thin prop-passing over react-leaflet, already
// verified by a clean production build; mock it here so this test can
// thoroughly exercise the app's own logic — state wiring, panels, the
// WebSocket data flow, and forms — without fighting jsdom's SVG gaps.
vi.mock("./components/MapView.jsx", () => ({
  default: (props) => (
    <div data-testid="mock-map">
      {props.ships.map((s) => (
        <div key={s.id} onClick={() => props.onSelectShip(s.id)}>{s.name}</div>
      ))}
    </div>
  ),
}));

const App = (await import("./App.jsx")).default;

describe("App smoke test", () => {
  it("renders Command view without crashing and shows the fleet", async () => {
    render(<App />);
    expect(screen.getByText(/Fleet Command/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText(/MV Test Ship/i).length).toBeGreaterThan(0));
  });

  it("selecting a ship shows the Command detail panel with directive controls", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getAllByText(/MV Test Ship/i).length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByText(/MV Test Ship/i)[0]);
    await waitFor(() => expect(screen.getByText(/Send directive to captain/i)).toBeInTheDocument());
  });

  it("switches to Captain role and shows the bridge panel", async () => {
    render(<App />);
    fireEvent.click(screen.getByText("Captain"));
    await waitFor(() => expect(screen.getByText(/Bridge/i)).toBeInTheDocument());
    expect(screen.getByText(/Report distress/i)).toBeInTheDocument();
    expect(screen.getByText(/Request assistance from nearby ships/i)).toBeInTheDocument();
  });

  it("alert feed shows empty state with no alerts", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText(/No alerts yet/i)).toBeInTheDocument());
  });
});
