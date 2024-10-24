/** @odoo-module */

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";

import { GridCell } from "../grid_cell";
import { GridRow } from "../grid_row/grid_row";

const gridComponentRegistry = registry.category("grid_components");

export class GridComponent extends Component {
    static props = ["name", "type", "isMeasure?", "component?", "*"];
    static template = "web_grid.GridComponent"

    get gridComponent() {
        console.log("\n\n\n\n\n\ncalllllllllllllll6666666666")
        if (this.props.component) {
            console.log("propssssssssssssssssss", this.props)
            return this.props.component;
        }
        if (gridComponentRegistry.contains(this.props.type)) {
            return gridComponentRegistry.get(this.props.type).component;
        }
        if (this.props.isMeasure) {
            console.warn(`Missing widget: ${this.props.type} for grid component`);
            return GridCell;
        }
        console.log("GridRowGridRowGridRowGridRowGridRow", GridRow);
        return GridRow;
    }

    get gridComponentProps() {
        console.log("//////////////////////", this.gridComponent.props);
        const gridComponentProps = Object.fromEntries(
            Object.entries(this.props).filter(
                ([key,]) => key in this.gridComponent.props
            )
        );
        gridComponentProps.classNames = `o_grid_component o_grid_component_${this.props.type} ${gridComponentProps.classNames || ""}`;
        return gridComponentProps;
    }
}
