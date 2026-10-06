import {
  BadRequestException, Body, CanActivate, ConflictException, Controller, ExecutionContext,
  Get, Injectable, Module, NotFoundException, Param, Patch, Post, Query, Req, UnauthorizedException, UseGuards,
} from "@nestjs/common";
import { ApiProperty, ApiTags } from "@nestjs/swagger";
import { Prisma, PrismaClient } from "@prisma/client";
import { IsArray, IsBoolean, IsIn, IsNumber, IsObject, IsOptional, IsString, Max, Min, ValidateNested } from "class-validator";
import { Type } from "class-transformer";
import {
  ENTITY_TYPES, RULE_VERSION, generateRequirements, validateOwnership,
  type AccountCase, type AccountProfile, type CaseStatus, type EntityType, type Party, type UsTaxClass,
} from "@fcc/domain";

type ActorRequest = Request & { actor: { id: string; roles: string[] } };

@Injectable()
class AuthGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean {
    const request = context.switchToHttp().getRequest<ActorRequest & { headers: Record<string, string | undefined> }>();
    if ((process.env.AUTH_MODE ?? "mock") !== "mock") {
      throw new UnauthorizedException("Entra ID adapter is required when AUTH_MODE is not mock.");
    }
    request.actor = {
      id: request.headers["x-user-id"] ?? "local-advisor",
      roles: (request.headers["x-user-roles"] ?? "Advisor").split(","),
    };
    return true;
  }
}

@Injectable()
class PrismaService extends PrismaClient {
  async onModuleInit() { await this.$connect(); }
  async onModuleDestroy() { await this.$disconnect(); }
}

class PartyDto implements Party {
  @ApiProperty() @IsString() id: string;
  @ApiProperty({ nullable: true }) @IsOptional() @IsString() parentId: string | null;
  @ApiProperty({ enum: ["ENTITY", "PERSON"] }) @IsIn(["ENTITY", "PERSON"]) kind: "ENTITY" | "PERSON";
  @ApiProperty() @IsString() legalName: string;
  @ApiProperty({ required: false, enum: ENTITY_TYPES }) @IsOptional() @IsIn(ENTITY_TYPES) entityType?: EntityType;
  @ApiProperty({ required: false }) @IsOptional() @IsString() country?: string;
  @ApiProperty({ required: false }) @IsOptional() @IsString() title?: string;
  @ApiProperty({ required: false, enum: ["complex", "simple", "unsure"] }) @IsOptional() @IsIn(["complex", "simple", "unsure"]) usTaxClass?: UsTaxClass;
  @ApiProperty() @IsNumber() @Min(0) @Max(100) ownershipPercent: number;
  @ApiProperty() @IsBoolean() isController: boolean;
  @ApiProperty() @IsBoolean() isSigningAuthority: boolean;
  @ApiProperty() @IsBoolean() isUsPerson: boolean;
  @ApiProperty() @IsBoolean() isPepHio: boolean;
}

class CreateCaseDto {
  @ApiProperty() @IsString() legalName: string;
  @ApiProperty({ enum: ENTITY_TYPES }) @IsIn(ENTITY_TYPES) entityType: EntityType;
}

class UpdateCaseDto {
  @ApiProperty() @IsNumber() @Min(1) version: number;
  @ApiProperty({ required: false }) @IsOptional() @IsString() legalName?: string;
  @ApiProperty({ required: false, enum: ["BUILDING", "DOCS_REQUESTED", "READY_FOR_COMPLIANCE"] })
  @IsOptional() @IsIn(["BUILDING", "DOCS_REQUESTED", "READY_FOR_COMPLIANCE"]) status?: CaseStatus;
  @ApiProperty({ required: false, type: [PartyDto] }) @IsOptional() @IsArray() @ValidateNested({ each: true }) @Type(() => PartyDto) parties?: PartyDto[];
  @ApiProperty({ required: false }) @IsOptional() @IsObject() profile?: AccountProfile;
  @ApiProperty({ required: false }) @IsOptional() @IsObject() checklist?: Record<string, boolean>;
}

class DocumentDto {
  @IsString() requirementId: string;
  @IsString() fileName: string;
  @IsString() contentType: string;
  @IsNumber() @Min(1) @Max(25_000_000) sizeBytes: number;
  @IsString() sha256: string;
}

function toDomain(row: {
  id: string; version: number; legalName: string; entityType: string; status: string;
  ruleVersion: string; parties: Prisma.JsonValue; profile: Prisma.JsonValue | null;
  checklist: Prisma.JsonValue; createdAt: Date; updatedAt: Date;
}): AccountCase & { checklist: Record<string, boolean> } {
  return {
    id: row.id, version: row.version, legalName: row.legalName, entityType: row.entityType as EntityType,
    status: row.status as CaseStatus, ruleVersion: row.ruleVersion, parties: row.parties as unknown as Party[],
    profile: (row.profile ?? undefined) as unknown as AccountProfile | undefined,
    checklist: row.checklist as Record<string, boolean>,
    createdAt: row.createdAt.toISOString(), updatedAt: row.updatedAt.toISOString(),
  };
}

@Injectable()
class CasesService {
  constructor(private readonly prisma: PrismaService) {}

  list(status?: string) {
    return this.prisma.accountCase.findMany({
      where: status ? { status } : undefined, orderBy: { updatedAt: "desc" }, take: 100,
      select: { id: true, version: true, legalName: true, entityType: true, status: true, updatedAt: true },
    });
  }

  async get(id: string) {
    const row = await this.prisma.accountCase.findUnique({ where: { id } });
    if (!row) throw new NotFoundException("Account case not found.");
    return toDomain(row);
  }

  async create(dto: CreateCaseDto, actorId: string) {
    const root: Party = {
      id: crypto.randomUUID(), parentId: null, kind: "ENTITY", legalName: dto.legalName,
      entityType: dto.entityType, ownershipPercent: 100, isController: true,
      isSigningAuthority: false, isUsPerson: false, isPepHio: false,
    };
    const row = await this.prisma.accountCase.create({
      data: { legalName: dto.legalName, entityType: dto.entityType, ruleVersion: RULE_VERSION, parties: [root] as unknown as Prisma.InputJsonValue, createdBy: actorId },
    });
    await this.audit(row.id, actorId, "CASE_CREATED", null, row);
    return toDomain(row);
  }

  async update(id: string, dto: UpdateCaseDto, actorId: string) {
    const before = await this.get(id);
    if (before.version !== dto.version) throw new ConflictException({ message: "Case changed by another user.", currentVersion: before.version });
    const nextParties = dto.parties ?? before.parties;
    const issues = validateOwnership({ parties: nextParties });
    if (dto.status === "READY_FOR_COMPLIANCE" && issues.length) throw new BadRequestException({ message: "Ownership structure is incomplete.", issues });
    const result = await this.prisma.accountCase.updateMany({
      where: { id, version: dto.version },
      data: {
        legalName: dto.legalName, status: dto.status, parties: dto.parties as unknown as Prisma.InputJsonValue,
        profile: dto.profile as unknown as Prisma.InputJsonValue, checklist: dto.checklist as unknown as Prisma.InputJsonValue,
        version: { increment: 1 },
      },
    });
    if (!result.count) throw new ConflictException("Concurrent update detected.");
    const after = await this.get(id);
    await this.audit(id, actorId, "CASE_UPDATED", before, after);
    return after;
  }

  async requirements(id: string) {
    const account = await this.get(id);
    return { ruleVersion: account.ruleVersion, validation: validateOwnership(account), requirements: generateRequirements(account) };
  }

  async addDocument(id: string, dto: DocumentDto, actorId: string) {
    await this.get(id);
    const objectKey = `${id}/${crypto.randomUUID()}-${dto.fileName.replace(/[^a-zA-Z0-9._-]/g, "_")}`;
    const document = await this.prisma.document.create({ data: { accountId: id, uploadedBy: actorId, objectKey, ...dto } });
    await this.audit(id, actorId, "DOCUMENT_REGISTERED", null, { id: document.id, requirementId: dto.requirementId, objectKey });
    return { document, upload: { method: "PUT", url: `${process.env.OBJECT_STORAGE_ENDPOINT ?? "http://localhost:10000/devstoreaccount1"}/${objectKey}`, expiresInSeconds: 900 } };
  }

  private audit(accountId: string, actorId: string, action: string, before: unknown, after: unknown) {
    return this.prisma.auditEvent.create({
      data: { accountId, actorId, action, before: (before ?? undefined) as Prisma.InputJsonValue, after: (after ?? undefined) as Prisma.InputJsonValue },
    });
  }
}

@ApiTags("cases")
@Controller("cases")
@UseGuards(AuthGuard)
class CasesController {
  constructor(private readonly cases: CasesService) {}

  @Get() list(@Query("status") status?: string) { return this.cases.list(status); }
  @Post() create(@Body() dto: CreateCaseDto, @Req() req: ActorRequest) { return this.cases.create(dto, req.actor.id); }
  @Get(":id") get(@Param("id") id: string) { return this.cases.get(id); }
  @Patch(":id") update(@Param("id") id: string, @Body() dto: UpdateCaseDto, @Req() req: ActorRequest) { return this.cases.update(id, dto, req.actor.id); }
  @Get(":id/requirements") requirements(@Param("id") id: string) { return this.cases.requirements(id); }
  @Post(":id/documents") addDocument(@Param("id") id: string, @Body() dto: DocumentDto, @Req() req: ActorRequest) { return this.cases.addDocument(id, dto, req.actor.id); }
}

@Controller("health")
export class HealthController {
  @Get() health() { return { status: "ok", service: "complex-account-api" }; }
}

@Module({
  controllers: [CasesController, HealthController],
  providers: [PrismaService, CasesService, AuthGuard],
})
export class AppModule {}
