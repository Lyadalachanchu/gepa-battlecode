package modular_seed;

import battlecode.common.*;

/**
 * Combat component: exploring toward enemies, attacking, carrying/throwing
 * rats, and cat avoidance while aggressive. Extracted verbatim from the
 * original seed's runExploreAndAttack.
 */
public class Combat {
    public static void runExploreAndAttack(RobotController rc) throws GameActionException {
        Message[] squeaks = rc.readSqueaks(rc.getRoundNum());

        for (Message msg : squeaks) {
            int rawSqueak = msg.getBytes();

            if (Strategy.getSqueakType(rawSqueak) != Strategy.SqueakType.CAT_FOUND) {
                continue;
            }

            int dirOrdinal = Strategy.getSqueakValue(rawSqueak);
            Direction toCat = RobotPlayer.directions[dirOrdinal];
            Direction away = toCat.opposite();

            if (rc.canTurn(away)) {
                rc.turn(away);
                break;
            }

            if (rc.canRemoveDirt(rc.getLocation().add(away))) {
                rc.removeDirt(rc.getLocation().add(away));
            }

            if (rc.canMove(away)) {
                rc.move(away);
                break;
            }
        }

        Navigation.moveRandom(rc);

        if (rc.canThrowRat() && RobotPlayer.turnsSinceCarry >= 3) {
            rc.throwRat();
        }

        for (Direction dir : RobotPlayer.directions) {
            MapLocation loc = rc.getLocation().add(dir);

            if (rc.canCarryRat(loc)) {
                rc.carryRat(loc);
                RobotPlayer.turnsSinceCarry = 0;
            }

            if (rc.canAttack(loc)) {
                rc.attack(loc);
            }
        }

        if (RobotPlayer.rand.nextDouble() < 0.1) {
            RobotPlayer.currentState = RobotPlayer.State.BUILD_TRAPS;
        }

        RobotInfo[] nearbyEnemies = rc.senseNearbyRobots(rc.getType().getVisionRadiusSquared(), rc.getTeam().opponent());
        RobotInfo[] nearbyCats = rc.senseNearbyRobots(rc.getType().getVisionRadiusSquared(), Team.NEUTRAL);

        for (RobotInfo enemy : nearbyEnemies) {
            if (enemy.getType().isRatKingType()) {
                // TODO found enemy rat king, message your own king
                RobotPlayer.currentState = RobotPlayer.State.RETURN_TO_KING_THEN_EXPLORE;
            }
        }

        int numEnemies = nearbyEnemies.length;
        if (numEnemies > 0) {
            rc.squeak(Strategy.getSqueak(Strategy.SqueakType.ENEMY_COUNT, numEnemies));
        }

        if (nearbyCats.length > 0) {
            // if distance squared to cat >= 17
            if (rc.getLocation().distanceSquaredTo(nearbyCats[0].getLocation()) >= 17) {
                Direction toCat = rc.getLocation().directionTo(nearbyCats[0].getLocation());
                rc.squeak(Strategy.getSqueak(Strategy.SqueakType.CAT_FOUND, toCat.ordinal()));
            } else {
                Direction away = rc.getLocation().directionTo(nearbyCats[0].getLocation()).opposite();
                if (rc.canTurn(away)) {
                    rc.turn(away);
                }

                if (rc.canRemoveDirt(rc.getLocation().add(away))) {
                    rc.removeDirt(rc.getLocation().add(away));
                }

                if (rc.canMove(away)) {
                    rc.move(away);
                }
            }
        }
    }
}
