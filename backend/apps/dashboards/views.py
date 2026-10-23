from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.rbac import has_perm
from apps.core.resource_api import scoped_get

from . import services


class OwnerOverview(APIView):
    required_permission = "dashboard.owner.view"

    def get(self, request):
        return Response(services.owner_overview(request, request.query_params.get("period", "today")))


class MachineBoard(APIView):
    required_permission = ["dashboard.owner.view", "dashboard.department.view"]

    def get(self, request):
        return Response(services.machine_board(include_commercial=has_perm(request.user, "masters.customer.view")))


class TvView(APIView):
    required_permission = "dashboard.tv.view"

    def get(self, request):
        return Response(services.tv_projection())


class GateOverview(APIView):
    required_permission = "dashboard.gate.view"

    def get(self, request):
        return Response(services.gate_overview(request))


class StoresDaily(APIView):
    required_permission = "inventory.stockmove.view"

    def get(self, request):
        d = parse_date(request.query_params.get("date", "") or "")
        s = request.query_params.get("shift") or None
        return Response(services.stores_daily(d, s if s in {"day", "night"} else None))


class StockInventory(APIView):
    required_permission = "inventory.stockbalance.view"

    def get(self, request):
        from apps.inventory.services import stock_overview
        return Response(stock_overview(include_value=has_perm(request.user, "commercial.cost.view")))


class Shortages(APIView):
    required_permission = ["purchase.requisition.view", "dashboard.owner.view", "planning.planline.view"]

    def get(self, request):
        from apps.purchase.services import shortage_report
        return Response(shortage_report())


class PlanningBoard(APIView):
    required_permission = "planning.planline.view"

    def get(self, request):
        d = parse_date(request.query_params.get("date", "") or "")
        return Response(services.planning_board(d, int(request.query_params.get("days", 3))))


class LabelStore(APIView):
    required_permission = "inventory.stockbalance.view"

    def get(self, request):
        from apps.inventory.resources import label_store_summary
        return Response(label_store_summary())


class TaskAnalytics(APIView):
    required_permission = "tasks.task.view"

    def get(self, request):
        return Response(services.task_analytics(request))


class Matching(APIView):
    required_permission = "fg.packingrecord.view"

    def get(self, request, product_id):
        from apps.fg.services import matching_summary
        product = scoped_get(request, "masters.product", product_id)
        return Response({"product": product.code, "variants": matching_summary(product)})


class Trace(APIView):
    """End-to-end search by lot / PR / SO / dispatch number - obeys the same access barriers (p.42)."""

    required_permission = "inventory.lot.view"

    def get(self, request):
        from apps.inventory.models import Lot
        from apps.inventory.trace import affected_by_material_lot, trace_lot
        q = (request.query_params.get("q") or "").strip()
        lot = Lot.objects.filter(doc_no__iexact=q).first() or Lot.objects.filter(supplier_lot__iexact=q).first()
        if not lot:
            return Response({"found": False})
        lot = scoped_get(request, "inventory.lot", lot.pk)
        return Response({"found": True, "lot": lot.doc_no, "trace": trace_lot(request, lot), "affected": affected_by_material_lot(request, lot)})
