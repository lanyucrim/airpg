export type MapCoordinate = {
  xKm: number;
  yKm: number;
  basis: string;
};

export type MapRegion = {
  regionId: string;
  name: string;
  anchor: [number, number];
  note: string;
};

export type MapStreet = {
  streetId: string;
  name: string;
  regionId: string | null;
  regionIds: string[];
  certainty: "canon" | "atlas_design";
  centerline: Record<string, unknown>;
  sequence: number;
};

export type MapStreetConnection = {
  fromStreetId: string;
  toStreetId: string;
  distanceKm: number;
  junction: string;
  status: "canon" | "inferred";
};

export type MapLocationLink = {
  fromLocationId: string;
  toLocationId: string;
  streetId: string;
  distanceKm: number;
  status: "canon" | "inferred";
};

export type MapLocation = {
  locationId: string;
  name: string;
  kind: string;
  parentLocationId: string | null;
  isCurrent: boolean;
  isReachable: boolean;
  atlasLocationId?: string;
  atlasStructureId?: string;
  atlasStreetId?: string;
  regionId?: string;
  coordinate?: MapCoordinate;
  streetIds?: string[];
  streetPositionM?: number;
  sourceStatus?: "canon" | "inferred";
  structureCount?: number;
  exits: {
    exitId: string;
    toLocationId: string;
    name: string;
    label: string;
    travelMinutes: number;
    baseTravelMinutes: number;
    weatherDelayMinutes: number;
    estimatedTravelMinutes: number;
    weatherCondition: string | null;
    weatherConditionName: string | null;
    locked: boolean;
  }[];
  visibleCharacters: { characterId: string; name: string; role: string }[];
  contents?: {
    characterIds: string[];
    containerCount: number;
    itemCount: number;
    carriedItemCount: number;
  };
};

export type PublicMap = {
  currentLocationId: string | null;
  displayLocationId?: string | null;
  currentLocationName?: string | null;
  displayLocationName?: string | null;
  /** Exact actor breadcrumb; unlike displayLocationName this is not room-collapsed. */
  locationPath?: string[];
  currentLocationDisplayName?: string | null;
  currentStructureName?: string | null;
  atlasId?: string;
  atlasSchemaVersion?: number;
  coordinateSystem?: Record<string, unknown>;
  speedModel?: Record<string, number>;
  regions?: MapRegion[];
  streets?: MapStreet[];
  streetConnections?: MapStreetConnection[];
  locationLinks?: MapLocationLink[];
  locations: MapLocation[];
};
