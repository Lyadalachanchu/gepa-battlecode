package modular_seed;

import battlecode.common.*;

/**
 * Defense component: trap placement around the current location (king
 * protection). Extracted verbatim from the original seed's runBuildTraps.
 */
public class Defense {
    public static void runBuildTraps(RobotController rc) throws GameActionException {
        for (Direction dir : RobotPlayer.directions) {
            MapLocation loc = rc.getLocation().add(dir);
            boolean catTraps = RobotPlayer.rand.nextBoolean();

            if (catTraps && rc.canPlaceCatTrap(loc)) {
                System.out.println("Built cat trap at " + loc);
                rc.placeCatTrap(loc);
            } else if (rc.canPlaceRatTrap(loc)) {
                System.out.println("Built rat trap at " + loc);
                rc.placeRatTrap(loc);
            }
        }

        if (RobotPlayer.rand.nextDouble() < 0.1) {
            RobotPlayer.currentState = RobotPlayer.State.EXPLORE_AND_ATTACK;
        }

        Navigation.moveRandom(rc);
    }
}
