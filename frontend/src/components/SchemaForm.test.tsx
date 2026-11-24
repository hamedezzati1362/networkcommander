import { render, screen, fireEvent } from "@testing-library/react";
import { SchemaForm } from "./SchemaForm";

const schema = {
  required: ["vlan_id"],
  properties: {
    module: { const: "vlan", default: "vlan" },
    kind: { enum: ["vlan_database", "svi", "vlan_interface"], default: "vlan_database", description: "type" },
    vlan_id: { type: "integer", minimum: 1, maximum: 4094 },
    name: { anyOf: [{ type: "string" }, { type: "null" }] },
    servers: { type: "array", items: { type: "string" } },
  },
};

test("renders fields from schema and hides the module discriminator", () => {
  render(<SchemaForm schema={schema} value={{}} onChange={() => {}} />);
  expect(screen.getByLabelText(/Vlan id/)).toBeInTheDocument();
  expect(screen.getByLabelText(/Name/)).toBeInTheDocument();
  expect(screen.queryByLabelText(/Module/)).toBeNull();
});

test("only offers kinds the device supports", () => {
  render(<SchemaForm schema={schema} value={{}} onChange={() => {}} allowed={{ kind: ["svi"] }} />);
  const sel = screen.getByLabelText(/Kind/) as HTMLSelectElement;
  expect([...sel.options].map((o) => o.value)).toEqual(["svi"]);
});

test("emits typed values and drops empty ones", () => {
  const onChange = vi.fn();
  render(<SchemaForm schema={schema} value={{ name: "x" }} onChange={onChange} />);
  fireEvent.change(screen.getByLabelText(/Vlan id/), { target: { value: "20" } });
  expect(onChange).toHaveBeenLastCalledWith({ name: "x", vlan_id: 20 });
  fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "" } });
  expect(onChange).toHaveBeenLastCalledWith({});
  fireEvent.change(screen.getByLabelText(/Servers/), { target: { value: "10.0.0.1, 10.0.0.2" } });
  expect(onChange).toHaveBeenLastCalledWith({ name: "x", servers: ["10.0.0.1", "10.0.0.2"] });
});
