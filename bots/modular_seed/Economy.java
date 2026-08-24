package modular_seed;

import battlecode.common.*;

/**
 * Economy component: cheese collection, cheese transfer to the king, and rat
 * spawning (the rat king's turn). Extracted verbatim from the original seed's
 * runRatKing / runFindCheese / runReturnToKing.
 */
public class Economy {
    public static void runRatKing(RobotController rc) throws GameActionException {
        int currentCost = rc.getCurrentRatCost();

        MapLocation[] potentialSpawnLocations = rc.getAllLocationsWithinRadiusSquared(rc.getLocation(), 8);
        boolean spawn = currentCost <= 10 || rc.getAllCheese() > currentCost + 2500;

        for (MapLocation loc : potentialSpawnLocations) {
            if (spawn && rc.canBuildRat(loc)) {
                rc.buildRat(loc);
                RobotPlayer.numRatsSpawned++;
                break;
            }

            if (rc.canPickUpCheese(loc)) {
                rc.pickUpCheese(loc);
                break;
            }
        }

        Message[] squeaks = rc.readSqueaks(rc.getRoundNum());

        for (Message msg : squeaks) {
            int rawSqueak = msg.getBytes();

            if (Strategy.getSqueakType(rawSqueak) != Strategy.SqueakType.CHEESE_MINE) {
                continue;
            }

            int encodedLoc = Strategy.getSqueakValue(rawSqueak);

            if (RobotPlayer.mineLocs.contains(encodedLoc)) {
                continue;
            }

            RobotPlayer.mineLocs.add(encodedLoc);
            int firstInt = Strategy.getFirstInt(encodedLoc);
            int lastInt = Strategy.getLastInt(encodedLoc);

            rc.writeSharedArray(2 * RobotPlayer.numMines + 2, firstInt);
            rc.writeSharedArray(2 * RobotPlayer.numMines + 3, lastInt);
            System.out.println("Writing to shared array: " + firstInt + ", " + lastInt);
            System.out.println("Cheese mine located at: " + Strategy.getX(encodedLoc) + ", " + Strategy.getY(encodedLoc));

            RobotPlayer.numMines++;
        }

        Navigation.moveRandom(rc);

        // TODO make more efficient and expand communication in the communication lecture
        rc.writeSharedArray(0, rc.getLocation().x);
        rc.writeSharedArray(1, rc.getLocation().y);
    }

    public static void runFindCheese(RobotController rc) throws GameActionException {
        if (!RobotPlayer.exploreWhenFindingCheese && RobotPlayer.numMines == 0) {
            RobotPlayer.exploreWhenFindingCheese = true;
        }

        if (RobotPlayer.targetCheeseMineLoc == null && !RobotPlayer.exploreWhenFindingCheese) {
            int cheeseMineIndex = RobotPlayer.rand.nextInt(RobotPlayer.numMines);
            int x = rc.readSharedArray(2 * cheeseMineIndex + 2);
            int y = rc.readSharedArray(2 * cheeseMineIndex + 3);
            int encodedLoc = 1024 * y + x;
            RobotPlayer.targetCheeseMineLoc = new MapLocation(Strategy.getX(encodedLoc), Strategy.getY(encodedLoc));
        }

        // search for cheese
        MapInfo[] nearbyInfos = rc.senseNearbyMapInfos();

        for (MapInfo info : nearbyInfos) {
            if (info.getCheeseAmount() > 0) {
                Direction toCheese = rc.getLocation().directionTo(info.getMapLocation());

                if (rc.canTurn(toCheese)) {
                    rc.turn(toCheese);
                    break;
                }
            } else if (info.hasCheeseMine()) {
                RobotPlayer.mineLoc = info.getMapLocation();
                System.out.println("Found cheese mine at " + RobotPlayer.mineLoc);
            }
        }

        for (Direction dir : RobotPlayer.directions) {
            MapLocation loc = rc.getLocation().add(dir);

            if (rc.canPickUpCheese(loc)) {
                rc.pickUpCheese(loc);

                if (rc.getRawCheese() >= 10) {
                    RobotPlayer.currentState = RobotPlayer.State.RETURN_TO_KING;
                }
            }
        }

        if (RobotPlayer.exploreWhenFindingCheese) {
            Navigation.moveRandom(rc);
        } else if (RobotPlayer.targetCheeseMineLoc != null) {
            Direction toTarget = rc.getLocation().directionTo(RobotPlayer.targetCheeseMineLoc);
            MapLocation nextLoc = rc.getLocation().add(toTarget);

            if (rc.canTurn(toTarget)) {
                rc.turn(toTarget);
            }

            if (rc.canRemoveDirt(nextLoc)) {
                rc.removeDirt(nextLoc);
            }

            // TODO replace with pathfinding for the pathfinding lecture
            if (rc.canMove(toTarget)) {
                rc.move(toTarget);
            }

            RobotPlayer.targetCheeseMineLoc = null;
        }
    }

    public static void runReturnToKing(RobotController rc) throws GameActionException {
        MapLocation kingLoc = new MapLocation(rc.readSharedArray(0), rc.readSharedArray(1));
        Direction toKing = rc.getLocation().directionTo(kingLoc);
        MapLocation nextLoc = rc.getLocation().add(toKing);

        if (rc.canTurn(toKing)) {
            rc.turn(toKing);
        }

        if (rc.canRemoveDirt(nextLoc)) {
            rc.removeDirt(nextLoc);
        }

        // TODO replace with pathfinding for the pathfinding lecture
        if (rc.canMove(toKing)) {
            rc.move(toKing);
        }

        int rawCheese = rc.getRawCheese();

        if (rawCheese == 0) {
            RobotPlayer.currentState = RobotPlayer.State.FIND_CHEESE;
            RobotPlayer.exploreWhenFindingCheese = RobotPlayer.rand.nextBoolean() && RobotPlayer.rand.nextBoolean();
        }

        if (rc.canSenseLocation(kingLoc)) {
            if (kingLoc.distanceSquaredTo(rc.getLocation()) <= 16 && RobotPlayer.mineLoc != null) {
                rc.squeak(Strategy.getSqueak(Strategy.SqueakType.CHEESE_MINE, Strategy.toInteger(RobotPlayer.mineLoc)));
            }

            RobotInfo[] kingLocations = rc.senseNearbyRobots(kingLoc, 8, rc.getTeam());

            for (RobotInfo robotInfo : kingLocations) {
                if (robotInfo.getType().isRatKingType()) {
                    MapLocation actualKingLoc = robotInfo.getLocation();

                    if (rc.canTransferCheese(actualKingLoc, rawCheese)) {
                        System.out.println("Transferred " + rawCheese + " cheese to king at " + kingLoc + ": I'm at " + rc.getLocation());
                        rc.transferCheese(actualKingLoc, rawCheese);
                        RobotPlayer.currentState = RobotPlayer.State.FIND_CHEESE;
                        RobotPlayer.exploreWhenFindingCheese = RobotPlayer.rand.nextBoolean() && RobotPlayer.rand.nextBoolean();
                    }

                    break;
                }
            }
        }
    }
}
