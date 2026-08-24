package modular_seed;

import battlecode.common.*;

/**
 * Navigation component: movement primitives (random-walk exploration with
 * dirt clearing). Extracted verbatim from the original seed's moveRandom.
 */
public class Navigation {
    public static void moveRandom(RobotController rc) throws GameActionException {
        MapLocation forwardLoc = rc.adjacentLocation(rc.getDirection());

        if (rc.canRemoveDirt(forwardLoc)) {
            rc.removeDirt(forwardLoc);
        }

        if (rc.canMoveForward()) {
            rc.moveForward();
        } else {
            Direction random = RobotPlayer.directions[RobotPlayer.rand.nextInt(RobotPlayer.directions.length)];

            if (rc.canTurn(random)) {
                rc.turn(random);
            }
        }
    }
}
