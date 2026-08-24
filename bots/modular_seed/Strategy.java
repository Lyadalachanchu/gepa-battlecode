package modular_seed;

import battlecode.common.*;

/**
 * Strategy component: the squeak (comms) protocol -- message typing and
 * location encoding shared by every other component. The original seed has no
 * cooperation/backstab policy code yet; when one is added it belongs here.
 * Extracted verbatim from the original seed's squeak/encoding helpers.
 */
public class Strategy {
    public static enum SqueakType {
        INVALID,
        ENEMY_RAT_KING,
        ENEMY_COUNT,
        CHEESE_MINE,
        CAT_FOUND,
    }

    public static SqueakType[] squeakTypes = SqueakType.values();

    public static int toInteger(MapLocation loc) {
        // loc.x is between 0 and 60
        // loc.y is between 0 and 60
        // ==> both can fit in 6 bits each
        return (loc.x << 6) | loc.y;
    }

    public static int getFirstInt(int loc) {
        // extract 10 smallest place value bits from toInteger(loc)
        return loc % 1024;
    }

    public static int getLastInt(int loc) {
        // extract bits with place values >= 2^10 from toInteger(loc)
        return loc >> 10;
    }

    public static int getX(int encodedLoc) {
        return encodedLoc >> 6;
    }

    public static int getY(int encodedLoc) {
        return encodedLoc % 64;
    }

    public static int getSqueak(SqueakType type, int value) {
        switch (type) {
            case ENEMY_RAT_KING:
                return (1 << 12) | value;
            case ENEMY_COUNT:
                return (2 << 12) | value;
            case CHEESE_MINE:
                return (3 << 12) | value;
            case CAT_FOUND:
                return (4 << 12) | value;
            default:
                return value;
        }
    }

    public static SqueakType getSqueakType(int rawSqueak) {
        return squeakTypes[rawSqueak >> 12];
    }

    public static int getSqueakValue(int rawSqueak) {
        return rawSqueak % 4096;
    }
}
